"""Unit tests for ``app.brain.skills.background``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.brain.llm_choice import LLMResponse
from app.brain.models import FeedSources, Users
from app.brain.skills.background import (
    BackgroundParserJob,
    FeedItem,
    RelevanceScore,
    _clean_text,
    _digest,
    _parse_json,
    _point_id,
)


def _owner() -> Users:
    return Users(
        user_id=uuid4(),
        username="owner",
        display_name="Владелец",
        passwd_hash="x",
        has_usable_password=False,
        role="owner",
        settings={},
    )


def _source(kind: str = "rss") -> FeedSources:
    return FeedSources(
        source_id=uuid4(),
        url="https://example.com/feed.xml",
        kind=kind,  # type: ignore[arg-type]
        title="Feed",
        is_active=True,
    )


def _item() -> FeedItem:
    return FeedItem(
        entry_id="entry-1",
        title="Новая модель LLM",
        summary="Коротко о релизе",
        link="https://example.com/1",
    )


def _session_factory(
    facts: list[Any] | None = None, owner: Users | None = None
) -> Any:
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = facts or []
    scalars_result.first.return_value = owner
    session.scalars = AsyncMock(return_value=scalars_result)

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


def _make_job(
    owner: Users | None = None,
    sources: list[Any] | None = None,
) -> tuple[
    BackgroundParserJob,
    AsyncMock,
    AsyncMock,
    AsyncMock,
    AsyncMock,
]:
    redis = AsyncMock()
    redis.claim_feed_item = AsyncMock(return_value=True)
    redis.consume_daily_quota = AsyncMock(return_value=True)
    router = AsyncMock()
    router.generate_with_fallback = AsyncMock(
        return_value=LLMResponse(text='{"score": 9, "summary": "ok"}')
    )
    router.embed = AsyncMock(return_value=[[0.1, 0.2]])
    vectors = AsyncMock()
    publisher = AsyncMock()
    job = BackgroundParserJob(
        redis_client=redis,
        session_factory=_session_factory(facts=sources, owner=owner),
        router=router,
        vectors=vectors,
        publisher=publisher,
    )
    return job, redis, router, vectors, publisher


class TestHelpers:
    def test_clean_text_strips_html_and_entities(self) -> None:
        result = _clean_text("<p>Hello &amp; <b>bye</b></p>")
        assert result == "Hello & bye"

    def test_parse_json_plain(self) -> None:
        assert _parse_json('{"score": 5}') == {"score": 5}

    def test_parse_json_fenced(self) -> None:
        raw = '```json\n{"score": 7, "summary": "x"}\n```'
        assert _parse_json(raw) == {"score": 7, "summary": "x"}

    def test_parse_json_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            _parse_json("not json at all")

    def test_digest_is_stable_and_source_scoped(self) -> None:
        source_id = uuid4()
        assert _digest(source_id, "e1") == _digest(source_id, "e1")
        assert _digest(source_id, "e1") != _digest(uuid4(), "e1")

    def test_point_id_fits_63_bits(self) -> None:
        value = _point_id("owner", uuid4(), "e1")
        assert 0 <= value < 2**63


class TestIsDue:
    def test_never_checked_is_due(self) -> None:
        assert BackgroundParserJob._is_due(_source(), datetime.now(UTC))

    def test_recent_check_is_not_due(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_SCAN_MINUTES", 60
        )
        source = _source()
        source.last_checked_at = datetime.now(UTC) - timedelta(minutes=5)

        assert not BackgroundParserJob._is_due(source, datetime.now(UTC))

    def test_expired_check_is_due(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_SCAN_MINUTES", 60
        )
        source = _source()
        source.last_checked_at = datetime.now(UTC) - timedelta(minutes=61)

        assert BackgroundParserJob._is_due(source, datetime.now(UTC))


class TestFilterRelevance:
    async def test_verdict_is_parsed(self) -> None:
        job, _, router, _, _ = _make_job()

        verdict = await job._filter_relevance(_owner(), _item())

        assert verdict is not None
        assert verdict.score == 9
        chat = router.generate_with_fallback.await_args.args[1]
        assert chat[0].role == "system"
        assert "Новая модель LLM" in chat[1].content

    async def test_bad_json_returns_none(self) -> None:
        job, _, router, _, _ = _make_job()
        router.generate_with_fallback = AsyncMock(
            return_value=LLMResponse(text="не json")
        )

        assert await job._filter_relevance(_owner(), _item()) is None

    async def test_router_failure_returns_none(self) -> None:
        job, _, router, _, _ = _make_job()
        router.generate_with_fallback = AsyncMock(
            side_effect=RuntimeError("llm down")
        )

        assert await job._filter_relevance(_owner(), _item()) is None


class TestHandleItem:
    async def test_high_score_sends_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_NOTIFY_SCORE", 8.0
        )
        job, redis, _, vectors, publisher = _make_job()
        job._filter_relevance = AsyncMock(  # type: ignore[method-assign]
            return_value=RelevanceScore(score=9, summary="важно")
        )

        processed = await job._handle_item(_owner(), _source(), _item())

        assert processed is True
        publisher.publish_outgoing.assert_awaited_once()
        outgoing = publisher.publish_outgoing.await_args.args[0]
        assert outgoing.channel == "telegram"
        assert "Новая модель LLM" in outgoing.text
        redis.consume_daily_quota.assert_awaited_once()
        vectors.upsert_points.assert_not_awaited()

    async def test_mid_score_is_remembered_in_qdrant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_NOTIFY_SCORE", 8.0
        )
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_STORE_SCORE", 5.0
        )
        job, _, _, vectors, publisher = _make_job()
        job._filter_relevance = AsyncMock(  # type: ignore[method-assign]
            return_value=RelevanceScore(score=6, summary="средне")
        )

        processed = await job._handle_item(_owner(), _source(), _item())

        assert processed is True
        publisher.publish_outgoing.assert_not_awaited()
        vectors.upsert_points.assert_awaited_once()

    async def test_low_score_is_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_NOTIFY_SCORE", 8.0
        )
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_STORE_SCORE", 5.0
        )
        job, _, _, vectors, publisher = _make_job()
        job._filter_relevance = AsyncMock(  # type: ignore[method-assign]
            return_value=RelevanceScore(score=1, summary="неинтересно")
        )

        processed = await job._handle_item(_owner(), _source(), _item())

        assert processed is True
        publisher.publish_outgoing.assert_not_awaited()
        vectors.upsert_points.assert_not_awaited()

    async def test_duplicate_entry_is_not_scored(self) -> None:
        job, redis, _, _, _ = _make_job()
        redis.claim_feed_item = AsyncMock(return_value=False)
        job._filter_relevance = AsyncMock()  # type: ignore[method-assign]

        processed = await job._handle_item(_owner(), _source(), _item())

        assert processed is False
        job._filter_relevance.assert_not_awaited()


class TestNotify:
    async def test_quota_exhausted_remembers_instead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_NOTIFY_SCORE", 8.0
        )
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_STORE_SCORE", 5.0
        )
        job, redis, _, vectors, publisher = _make_job()
        redis.consume_daily_quota = AsyncMock(return_value=False)
        job._filter_relevance = AsyncMock(  # type: ignore[method-assign]
            return_value=RelevanceScore(score=9, summary="важно")
        )

        processed = await job._handle_item(_owner(), _source(), _item())

        assert processed is True
        publisher.publish_outgoing.assert_not_awaited()
        vectors.upsert_points.assert_awaited_once()

    async def test_reaction_is_appended_after_news(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_NOTIFY_SCORE", 8.0
        )
        job, _, router, _, publisher = _make_job()
        router.generate_with_fallback = AsyncMock(
            return_value=LLMResponse(text="Нужно глянуть.")
        )
        job._filter_relevance = AsyncMock(  # type: ignore[method-assign]
            return_value=RelevanceScore(score=9, summary="важно")
        )

        await job._handle_item(_owner(), _source(), _item())

        text = publisher.publish_outgoing.await_args.args[0].text
        assert "Новая модель LLM" in text
        assert text.endswith("Нужно глянуть.")
        role = router.generate_with_fallback.await_args.args[0]
        assert role == "dialogue"

    async def test_reaction_failure_keeps_notification(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_NOTIFY_SCORE", 8.0
        )
        job, _, router, _, publisher = _make_job()
        router.generate_with_fallback = AsyncMock(
            side_effect=RuntimeError("dialogue down")
        )
        job._filter_relevance = AsyncMock(  # type: ignore[method-assign]
            return_value=RelevanceScore(score=9, summary="важно")
        )

        await job._handle_item(_owner(), _source(), _item())

        text = publisher.publish_outgoing.await_args.args[0].text
        assert "Новая модель LLM" in text
        assert "dialogue down" not in text


class TestRun:
    async def test_without_owner_is_noop(self) -> None:
        job, *_ = _make_job(owner=None)

        assert await job.run() == 0

    async def test_budget_is_shared_across_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_MAX_ITEMS_PER_RUN", 1
        )
        job, *_ = _make_job(owner=_owner(), sources=[_source(), _source()])
        job._fetch = AsyncMock(return_value=[_item()])  # type: ignore[method-assign]
        job._handle_item = AsyncMock(return_value=True)  # type: ignore[method-assign]

        processed = await job.run()

        assert processed == 1
        assert job._fetch.await_count == 1

    async def test_partially_handled_source_keeps_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.skills.background.settings.FEED_MAX_ITEMS_PER_RUN", 1
        )
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = [_source()]
        result.first.return_value = _owner()
        session.scalars = AsyncMock(return_value=result)

        @asynccontextmanager
        async def factory() -> Any:
            yield session

        job = BackgroundParserJob(
            redis_client=AsyncMock(),
            session_factory=factory,
            router=AsyncMock(),
            vectors=AsyncMock(),
            publisher=AsyncMock(),
        )
        job._fetch = AsyncMock(  # type: ignore[method-assign]
            return_value=[_item(), _item()]
        )
        job._handle_item = AsyncMock(return_value=True)  # type: ignore[method-assign]

        processed = await job.run()

        assert processed == 1
        session.execute.assert_not_awaited()
        session.commit.assert_not_awaited()
