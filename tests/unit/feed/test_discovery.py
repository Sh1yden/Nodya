"""Unit tests for ``app.brain.feed.discovery``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from app.brain.feed.discovery import FeedDiscoveryJob
from app.brain.feed.models import ResolvedSource
from app.brain.feed.service import AddResult
from app.brain.llm_choice import LLMResponse
from app.brain.models import FeedSources, Users
from app.brain.search.base import SearchResult

_QUERIES_JSON = '{"queries": ["ai news", "python news"]}'
_RANK_TOP_JSON = '{"selected": [{"index": 1, "score": 9.0}]}'
_RANK_LOW_JSON = '{"selected": [{"index": 1, "score": 4.0}]}'


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


def _session_factory(owner: Users | None) -> Any:
    session = AsyncMock()
    result = MagicMock()
    result.first.return_value = owner
    session.scalars = AsyncMock(return_value=result)

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    return factory


class _FakeSearch:
    def __init__(
        self, results: list[SearchResult], available: bool = True
    ) -> None:
        self.available = available
        self._results = results
        self.queries: list[str] = []

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        self.queries.append(query)
        return list(self._results)


class _FakeService:
    def __init__(
        self,
        *,
        active: int = 0,
        known: set[str] | None = None,
        status: str = "added",
    ) -> None:
        self.active = active
        self.known = known or set()
        self.status = status
        self.added: list[ResolvedSource] = []
        self.count_calls = 0

    async def count_active(self) -> int:
        self.count_calls += 1
        return self.active

    async def existing_urls(self) -> set[str]:
        return set(self.known)

    async def add_resolved(self, resolved: ResolvedSource) -> AddResult:
        self.added.append(resolved)
        source = FeedSources(
            source_id=uuid4(),
            url=resolved.url,
            kind=resolved.kind,
            title=resolved.title,
            is_active=True,
        )
        return AddResult(source=source, status=self.status)  # type: ignore[arg-type]


async def _default_resolver(url: str) -> ResolvedSource:
    host = urlsplit(url).netloc
    return ResolvedSource(
        url=f"https://{host}/feed", kind="rss", title=f"{host} news"
    )


def _make_job(
    monkeypatch: pytest.MonkeyPatch,
    *,
    owner: Users | None = None,
    facts: list[str] | None = None,
    results: list[SearchResult] | None = None,
    search_available: bool = True,
    service: _FakeService | None = None,
    responses: list[LLMResponse] | None = None,
    resolver: Any = None,
) -> tuple[FeedDiscoveryJob, AsyncMock, _FakeSearch, AsyncMock, _FakeService]:
    monkeypatch.setattr(
        "app.brain.feed.discovery.load_owner_fact_lines",
        AsyncMock(
            return_value=facts if facts is not None else ["tech | ai: интерес"]
        ),
    )
    router = AsyncMock()
    router.generate_with_fallback = AsyncMock(
        side_effect=(
            responses
            if responses is not None
            else [
                LLMResponse(text=_QUERIES_JSON),
                LLMResponse(text=_RANK_TOP_JSON),
            ]
        )
    )
    search = _FakeSearch(results or [], available=search_available)
    svc = service or _FakeService()
    publisher = AsyncMock()
    job = FeedDiscoveryJob(
        _session_factory(owner),
        router,
        search,  # type: ignore[arg-type]
        svc,  # type: ignore[arg-type]
        publisher,
        resolver=resolver or _default_resolver,
    )
    return job, router, search, publisher, svc


class TestGuards:
    async def test_disabled_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.feed.discovery.settings.FEED_DISCOVERY_ENABLED", False
        )
        job, router, _, publisher, service = _make_job(
            monkeypatch, owner=_owner()
        )

        assert await job.run() == 0
        router.generate_with_fallback.assert_not_awaited()
        assert service.count_calls == 0
        publisher.publish_outgoing.assert_not_awaited()

    async def test_without_owner_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job, _, _, _, service = _make_job(monkeypatch, owner=None)

        assert await job.run() == 0
        assert service.count_calls == 0

    async def test_without_search_providers_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job, router, _, _, _ = _make_job(
            monkeypatch, owner=_owner(), search_available=False
        )

        assert await job.run() == 0
        router.generate_with_fallback.assert_not_awaited()

    async def test_active_source_cap_stops_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.feed.discovery.settings.FEED_MAX_ACTIVE_SOURCES", 1
        )
        job, router, _, _, _ = _make_job(
            monkeypatch, owner=_owner(), service=_FakeService(active=1)
        )

        assert await job.run() == 0
        router.generate_with_fallback.assert_not_awaited()

    async def test_without_facts_is_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job, router, _, _, _ = _make_job(monkeypatch, owner=_owner(), facts=[])

        assert await job.run() == 0
        router.generate_with_fallback.assert_not_awaited()

    async def test_bad_query_json_stops_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job, _, search, _, service = _make_job(
            monkeypatch,
            owner=_owner(),
            responses=[LLMResponse(text="not json")],
        )

        assert await job.run() == 0
        assert search.queries == []
        assert service.added == []


class TestDiscoveryRun:
    async def test_happy_path_adds_and_reports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = [
            SearchResult(title="A", url="https://a.example/news"),
            SearchResult(title="A dup", url="https://a.example/other"),
            SearchResult(title="Known", url="https://known.example/post"),
            SearchResult(title="B", url="https://b.example/news"),
        ]
        known = {"https://known.example/feed"}
        job, router, search, publisher, service = _make_job(
            monkeypatch,
            owner=_owner(),
            results=results,
            service=_FakeService(known=known),
        )

        added = await job.run()

        assert added == 1
        assert search.queries == ["ai news", "python news"]
        assert [source.url for source in service.added] == [
            "https://a.example/feed"
        ]
        assert router.generate_with_fallback.await_count == 2
        publisher.publish_outgoing.assert_awaited_once()
        text = publisher.publish_outgoing.await_args.args[0].text
        assert "Нашла новые источники" in text
        assert "a.example news" in text

    async def test_low_score_adds_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = [SearchResult(title="A", url="https://a.example/news")]
        job, _, _, publisher, service = _make_job(
            monkeypatch,
            owner=_owner(),
            results=results,
            responses=[
                LLMResponse(text=_QUERIES_JSON),
                LLMResponse(text=_RANK_LOW_JSON),
            ],
        )

        assert await job.run() == 0
        assert service.added == []
        publisher.publish_outgoing.assert_not_awaited()

    async def test_known_resolved_url_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = [SearchResult(title="A", url="https://a.example/news")]
        known = {"https://a.example/feed"}

        async def resolver(url: str) -> ResolvedSource:
            return ResolvedSource(
                url="https://a.example/feed", kind="rss", title="A"
            )

        job, router, _, publisher, service = _make_job(
            monkeypatch,
            owner=_owner(),
            results=results,
            service=_FakeService(known=known),
            resolver=resolver,
            responses=[LLMResponse(text=_QUERIES_JSON)],
        )

        assert await job.run() == 0
        assert router.generate_with_fallback.await_count == 1
        assert service.added == []
        publisher.publish_outgoing.assert_not_awaited()

    async def test_existing_status_is_not_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = [SearchResult(title="A", url="https://a.example/news")]
        job, _, _, publisher, service = _make_job(
            monkeypatch,
            owner=_owner(),
            results=results,
            service=_FakeService(status="exists"),
        )

        assert await job.run() == 0
        assert len(service.added) == 1
        publisher.publish_outgoing.assert_not_awaited()

    async def test_add_failure_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = [SearchResult(title="A", url="https://a.example/news")]
        service = _FakeService()

        async def _fail(resolved: ResolvedSource) -> AddResult:
            raise RuntimeError("db down")

        service.add_resolved = _fail  # type: ignore[method-assign]
        job, _, _, publisher, _ = _make_job(
            monkeypatch, owner=_owner(), results=results, service=service
        )

        assert await job.run() == 0
        publisher.publish_outgoing.assert_not_awaited()

    async def test_cap_limits_additions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.feed.discovery.settings.FEED_MAX_ACTIVE_SOURCES", 2
        )
        monkeypatch.setattr(
            "app.brain.feed.discovery.settings.FEED_DISCOVERY_MAX_SOURCES", 2
        )
        results = [
            SearchResult(title="A", url="https://a.example/news"),
            SearchResult(title="B", url="https://b.example/news"),
        ]
        job, _, _, _, service = _make_job(
            monkeypatch,
            owner=_owner(),
            results=results,
            service=_FakeService(active=1),
            responses=[
                LLMResponse(text=_QUERIES_JSON),
                LLMResponse(
                    text=(
                        '{"selected": [{"index": 1, "score": 9}, '
                        '{"index": 2, "score": 9}]}'
                    )
                ),
            ],
        )

        assert await job.run() == 1
        assert len(service.added) == 1
