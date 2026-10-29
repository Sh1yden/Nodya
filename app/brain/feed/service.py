"""Feed source management shared by commands, CLI and discovery."""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.exc import IntegrityError

from app.brain.feed.models import ResolvedSource
from app.brain.feed.resolver import (
    FeedResolutionError,
    normalize_raw_url,
    resolve_source,
)
from app.brain.models import FeedSources
from app.brain.repositories import FeedSourcesRepo
from app.core import settings

_SHORT_ID_MIN = 4


class FeedSourceLimitError(ValueError):
    """The active-source cap is reached."""


@dataclass(slots=True)
class AddResult:
    """Outcome of adding a source."""

    source: FeedSources
    status: Literal["added", "reactivated", "exists"]


@dataclass(slots=True)
class DeactivateResult:
    """Outcome of switching a source off."""

    source: FeedSources
    changed: bool


class FeedSourceService:
    """Add, list and deactivate feed sources."""

    def __init__(
        self, session_factory: Any, resolver: Any = resolve_source
    ) -> None:
        """Bind the service to a session factory.

        Args:
            session_factory: Async session factory for DB access.
            resolver: URL resolver callable (tests override it).
        """
        self._session_factory = session_factory
        self._resolver = resolver

    async def add(self, raw_url: str) -> AddResult:
        """Resolve a user URL and register the source.

        Args:
            raw_url: URL supplied by the owner or discovery.

        Returns:
            Add result with the persisted source.

        Raises:
            FeedResolutionError: URL is unsupported or has no feed.
            FeedSourceLimitError: Active-source cap is reached.
        """
        resolved = await self._resolver(raw_url)
        return await self.add_resolved(resolved)

    async def add_resolved(self, resolved: ResolvedSource) -> AddResult:
        """Register an already resolved source.

        Args:
            resolved: Canonical source from the resolver.

        Returns:
            Add result with the persisted source.

        Raises:
            FeedSourceLimitError: Active-source cap is reached.
        """
        async with self._session_factory() as session:
            repo = FeedSourcesRepo(session)
            existing = await repo.get_by_url(resolved.url)
            if existing is not None:
                return await self._handle_existing(session, existing, resolved)
            await self._ensure_capacity(repo)
            source = FeedSources(
                url=resolved.url,
                kind=resolved.kind,
                title=_title(resolved),
            )
            repo.add(source)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await repo.get_by_url(resolved.url)
                if existing is None:
                    raise
                return AddResult(source=existing, status="exists")
            return AddResult(source=source, status="added")

    async def _handle_existing(
        self, session: Any, existing: FeedSources, resolved: ResolvedSource
    ) -> AddResult:
        """Reactivate or report an already known source."""
        if existing.is_active:
            return AddResult(source=existing, status="exists")
        repo = FeedSourcesRepo(session)
        await self._ensure_capacity(repo)
        existing.is_active = True
        if not existing.title and resolved.title:
            existing.title = _title(resolved)
        await session.commit()
        return AddResult(source=existing, status="reactivated")

    async def _ensure_capacity(self, repo: FeedSourcesRepo) -> None:
        """Reject a new active source above the configured cap."""
        active = await repo.count_active()
        if active >= settings.FEED_MAX_ACTIVE_SOURCES:
            raise FeedSourceLimitError(
                "Достигнут лимит активных источников "
                f"({settings.FEED_MAX_ACTIVE_SOURCES}). "
                "Отключи лишние: /feed rm <id>."
            )

    async def list(self) -> list[FeedSources]:
        """Return every source, including disabled ones."""
        async with self._session_factory() as session:
            return await FeedSourcesRepo(session).list_all()

    async def existing_urls(self) -> set[str]:
        """Return canonical URLs of all known sources."""
        return {source.url for source in await self.list()}

    async def count_active(self) -> int:
        """Return the number of active sources."""
        async with self._session_factory() as session:
            return await FeedSourcesRepo(session).count_active()

    async def deactivate(self, ref: str) -> DeactivateResult | None:
        """Switch a source off by short id or URL.

        Args:
            ref: UUID, UUID prefix or canonical/source URL.

        Returns:
            Result with the source, or None when not found.
        """
        async with self._session_factory() as session:
            sources = await FeedSourcesRepo(session).list_all()
            source = _match_source(sources, ref)
            if source is None:
                return None
            changed = source.is_active
            if changed:
                source.is_active = False
                await session.commit()
            return DeactivateResult(source=source, changed=changed)


def _title(resolved: ResolvedSource) -> str | None:
    """Trim a resolved title to the column limit."""
    if not resolved.title:
        return None
    return resolved.title[:256] or None


def _match_source(sources: list[FeedSources], ref: str) -> FeedSources | None:
    """Find a source by UUID prefix or URL."""
    needle = ref.strip()
    lowered = needle.lower()
    if _is_short_id(lowered):
        for source in sources:
            if source.source_id.hex.startswith(lowered):
                return source
    try:
        normalized = normalize_raw_url(needle)
    except FeedResolutionError:
        normalized = needle
    for source in sources:
        if source.url == normalized:
            return source
    return None


def _is_short_id(value: str) -> bool:
    """Whether a reference looks like a UUID prefix."""
    if len(value) < _SHORT_ID_MIN or len(value) > 32:
        return False
    return all(char in string.hexdigits for char in value)
