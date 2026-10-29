"""Unit tests for ``app.brain.feed.service``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.brain.feed.models import ResolvedSource
from app.brain.feed.service import FeedSourceLimitError, FeedSourceService
from app.brain.models import FeedSources

_FEED_URL = "https://example.com/feed.xml"


def _row(
    url: str = _FEED_URL,
    *,
    kind: str = "rss",
    title: str | None = "Feed",
    is_active: bool = True,
) -> FeedSources:
    return FeedSources(
        source_id=uuid4(),
        url=url,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        is_active=is_active,
    )


class _FakeSession:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        if self._state.get("fail_next_commit"):
            self._state["fail_next_commit"] = False
            raise IntegrityError("INSERT", {}, Exception("duplicate"))
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _factory(state: dict[str, Any]) -> Any:
    session = _FakeSession(state)

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    factory.session = session  # type: ignore[attr-defined]
    return factory


def _patch_repo(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[FeedSources],
    *,
    hide_first_get: bool = False,
) -> None:
    calls = {"get": 0}

    class FakeRepo:
        def __init__(self, session: Any) -> None:
            pass

        async def list_all(self) -> list[FeedSources]:
            return list(rows)

        async def get_by_url(self, url: str) -> FeedSources | None:
            calls["get"] += 1
            if hide_first_get and calls["get"] == 1:
                return None
            return next((row for row in rows if row.url == url), None)

        async def count_active(self) -> int:
            return sum(1 for row in rows if row.is_active)

        def add(self, source: FeedSources) -> None:
            if source.source_id is None:
                source.source_id = uuid4()
            if source.is_active is None:
                source.is_active = True
            rows.append(source)

    monkeypatch.setattr("app.brain.feed.service.FeedSourcesRepo", FakeRepo)


async def _resolver(raw: str) -> ResolvedSource:
    return ResolvedSource(url=_FEED_URL, kind="rss", title="Resolved")


class TestAdd:
    async def test_new_source_is_stored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows: list[FeedSources] = []
        _patch_repo(monkeypatch, rows)
        service = FeedSourceService(_factory({}), resolver=_resolver)

        result = await service.add("example.com")

        assert result.status == "added"
        assert len(rows) == 1
        assert rows[0].url == _FEED_URL
        assert rows[0].kind == "rss"
        assert rows[0].title == "Resolved"

    async def test_existing_active_source_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = _row()
        rows = [existing]
        _patch_repo(monkeypatch, rows)
        service = FeedSourceService(_factory({}), resolver=_resolver)

        result = await service.add("example.com")

        assert result.status == "exists"
        assert result.source is existing
        assert len(rows) == 1

    async def test_disabled_source_is_reactivated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = _row(title=None, is_active=False)
        rows = [existing]
        _patch_repo(monkeypatch, rows)
        service = FeedSourceService(_factory({}), resolver=_resolver)

        result = await service.add("example.com")

        assert result.status == "reactivated"
        assert existing.is_active is True
        assert existing.title == "Resolved"

    async def test_cap_blocks_new_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.feed.service.settings.FEED_MAX_ACTIVE_SOURCES", 1
        )
        rows = [_row("https://other.example/feed.xml")]
        _patch_repo(monkeypatch, rows)
        service = FeedSourceService(_factory({}), resolver=_resolver)

        with pytest.raises(FeedSourceLimitError, match="лимит"):
            await service.add("example.com")

        assert len(rows) == 1

    async def test_cap_blocks_reactivation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.feed.service.settings.FEED_MAX_ACTIVE_SOURCES", 1
        )
        rows = [
            _row(is_active=False),
            _row("https://other.example/feed.xml", is_active=True),
        ]
        _patch_repo(monkeypatch, rows)
        service = FeedSourceService(_factory({}), resolver=_resolver)

        with pytest.raises(FeedSourceLimitError, match="лимит"):
            await service.add("example.com")

        assert rows[0].is_active is False

    async def test_commit_race_returns_existing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = _row()
        rows = [existing]
        _patch_repo(monkeypatch, rows, hide_first_get=True)
        state: dict[str, Any] = {"fail_next_commit": True}
        factory = _factory(state)
        service = FeedSourceService(factory, resolver=_resolver)

        result = await service.add("example.com")

        assert result.status == "exists"
        assert result.source is existing
        assert factory.session.rollbacks == 1


class TestQueries:
    async def test_existing_urls_include_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(is_active=True),
            _row("https://off.example/feed.xml", is_active=False),
        ]
        _patch_repo(monkeypatch, rows)
        service = FeedSourceService(_factory({}))

        assert await service.existing_urls() == {
            _FEED_URL,
            "https://off.example/feed.xml",
        }

    async def test_count_active_counts_only_active(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(is_active=True),
            _row("https://off.example/feed.xml", is_active=False),
        ]
        _patch_repo(monkeypatch, rows)
        service = FeedSourceService(_factory({}))

        assert await service.count_active() == 1


class TestDeactivate:
    async def test_short_id_disables_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _row()
        _patch_repo(monkeypatch, [source])
        service = FeedSourceService(_factory({}))

        result = await service.deactivate(source.source_id.hex[:8])

        assert result is not None
        assert result.changed is True
        assert source.is_active is False

    async def test_url_disables_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _row()
        _patch_repo(monkeypatch, [source])
        service = FeedSourceService(_factory({}))

        result = await service.deactivate(_FEED_URL)

        assert result is not None
        assert result.changed is True
        assert source.is_active is False

    async def test_already_disabled_reports_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _row(is_active=False)
        _patch_repo(monkeypatch, [source])
        service = FeedSourceService(_factory({}))

        result = await service.deactivate(source.source_id.hex[:8])

        assert result is not None
        assert result.changed is False

    async def test_unknown_reference_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_repo(monkeypatch, [_row()])
        service = FeedSourceService(_factory({}))

        assert await service.deactivate("deadbeef") is None
