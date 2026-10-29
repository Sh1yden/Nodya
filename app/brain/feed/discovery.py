"""Interest-based discovery of new feed sources (stage 7.4).

Weekly job: the owner's facts become search queries, the provider
chain finds candidate links, the resolver validates them and the BP
role keeps only genuinely relevant sources. Hard caps protect search
quotas and keep the source list small.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from app.api.messaging import MessagePublisher
from app.brain.feed.models import ResolvedSource
from app.brain.feed.profile import load_owner_fact_lines
from app.brain.feed.prompts import (
    DISCOVERY_QUERIES_SYSTEM,
    DISCOVERY_RANKING_SYSTEM,
)
from app.brain.feed.resolver import FeedResolutionError, resolve_source
from app.brain.feed.service import FeedSourceService
from app.brain.feed.text import parse_json
from app.brain.llm_choice import ChatMessage, LLMRouter
from app.brain.models import FeedSources, Users
from app.brain.search.base import SearchChain, SearchResult
from app.common import OutgoingMessage
from app.core import LoggerMixin, settings

_RESOLVE_CONCURRENCY = 3


class FeedDiscoveryJob(LoggerMixin):
    """Searches the web for new sources matching owner interests."""

    def __init__(
        self,
        session_factory: Any,
        router: LLMRouter,
        search_chain: SearchChain,
        service: FeedSourceService,
        publisher: MessagePublisher,
        resolver: Any = resolve_source,
    ) -> None:
        """Bind collaborators; no I/O happens here.

        Args:
            session_factory: Async session factory (owner, facts).
            router: LLM router (BP role for queries and ranking).
            search_chain: Web search chain.
            service: Feed source management service.
            publisher: Outgoing publisher for the report.
            resolver: URL resolver callable (tests override it).
        """
        self._session_factory = session_factory
        self._router = router
        self._search = search_chain
        self._service = service
        self._publisher = publisher
        self._resolver = resolver

    async def run(self) -> int:
        """Run one discovery pass.

        Returns:
            Number of sources added in this pass.
        """
        if not settings.FEED_DISCOVERY_ENABLED:
            self._lg.debug("Feed discovery is disabled.")
            return 0
        owner = await self._load_owner()
        if owner is None:
            self._lg.warning("Feed discovery skipped: no owner account.")
            return 0
        if not self._search.available:
            self._lg.warning("Feed discovery skipped: no search provider.")
            return 0
        active = await self._service.count_active()
        if active >= settings.FEED_MAX_ACTIVE_SOURCES:
            self._lg.info("Feed discovery skipped: active source cap reached.")
            return 0
        facts = await load_owner_fact_lines(
            self._session_factory, owner.user_id
        )
        if not facts:
            self._lg.info("Feed discovery skipped: no owner facts yet.")
            return 0
        queries = await self._generate_queries(owner, facts)
        if not queries:
            return 0
        results = await self._search_all(queries)
        known = await self._service.existing_urls()
        candidates = _collect_candidates(results, known)
        resolved = await self._resolve_candidates(candidates, known)
        if not resolved:
            self._lg.info("Feed discovery: no valid candidates.")
            return 0
        selected = await self._rank(owner, facts, resolved)
        added: list[FeedSources] = []
        for source in selected:
            if active + len(added) >= settings.FEED_MAX_ACTIVE_SOURCES:
                break
            try:
                result = await self._service.add_resolved(source)
            except Exception as exc:
                self._lg.warning(
                    f"Feed discovery add failed ({source.url}): {exc}"
                )
                continue
            if result.status == "added":
                added.append(result.source)
        if not added:
            self._lg.info("Feed discovery: nothing relevant found.")
            return 0
        await self._notify(owner, added)
        self._lg.info(f"Feed discovery added {len(added)} source(s).")
        return len(added)

    async def _load_owner(self) -> Users | None:
        """Fetch the single owner account."""
        async with self._session_factory() as session:
            result = await session.scalars(
                select(Users).where(Users.role == "owner")
            )
            return result.first()

    async def _generate_queries(
        self, owner: Users, facts: list[str]
    ) -> list[str]:
        """Ask the BP role for discovery search queries."""
        prompt = (
            f"Person: {owner.display_name or owner.username}\n"
            "Known facts:\n"
            f"{_fact_block(facts)}\n\n"
            f"Return at most {settings.FEED_DISCOVERY_QUERIES} queries."
        )
        try:
            response = await self._router.generate_with_fallback(
                "bp",
                [
                    ChatMessage(
                        role="system", content=DISCOVERY_QUERIES_SYSTEM
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
            )
            data = parse_json(response.text or "")
        except Exception as exc:
            self._lg.warning(f"Discovery query generation failed: {exc}")
            return []
        raw_queries = data.get("queries")
        if not isinstance(raw_queries, list):
            return []
        queries = [
            str(query).strip() for query in raw_queries if str(query).strip()
        ]
        return queries[: settings.FEED_DISCOVERY_QUERIES]

    async def _search_all(self, queries: list[str]) -> list[SearchResult]:
        """Run every query through the provider chain in parallel."""
        try:
            batches = await asyncio.gather(
                *(
                    self._search.search(
                        query, settings.FEED_DISCOVERY_MAX_RESULTS
                    )
                    for query in queries
                )
            )
        except Exception as exc:
            self._lg.warning(f"Discovery search failed: {exc}")
            return []
        return [result for batch in batches for result in batch]

    async def _resolve_candidates(
        self, candidates: list[SearchResult], known: set[str]
    ) -> list[ResolvedSource]:
        """Validate candidate links through the resolver."""
        semaphore = asyncio.Semaphore(_RESOLVE_CONCURRENCY)

        async def _resolve_one(
            candidate: SearchResult,
        ) -> ResolvedSource | None:
            async with semaphore:
                try:
                    return await self._resolver(candidate.url)
                except FeedResolutionError as exc:
                    self._lg.debug(
                        f"Discovery candidate rejected "
                        f"({candidate.url}): {exc}"
                    )
                except Exception as exc:
                    self._lg.warning(
                        f"Discovery resolve failed ({candidate.url}): {exc}"
                    )
                return None

        resolved = await asyncio.gather(
            *(_resolve_one(candidate) for candidate in candidates)
        )
        unique: list[ResolvedSource] = []
        seen: set[str] = set(known)
        for source in resolved:
            if source is None or source.url in seen:
                continue
            seen.add(source.url)
            unique.append(source)
        return unique

    async def _rank(
        self,
        owner: Users,
        facts: list[str],
        candidates: list[ResolvedSource],
    ) -> list[ResolvedSource]:
        """Keep only candidates the BP role finds genuinely relevant."""
        listing = "\n".join(
            f"{index}. {source.title or '(no title)'} — "
            f"{source.kind} — {source.url}"
            for index, source in enumerate(candidates, start=1)
        )
        prompt = (
            f"Person: {owner.display_name or owner.username}\n"
            "Known facts:\n"
            f"{_fact_block(facts)}\n\n"
            f"Candidates:\n{listing}"
        )
        try:
            response = await self._router.generate_with_fallback(
                "bp",
                [
                    ChatMessage(
                        role="system", content=DISCOVERY_RANKING_SYSTEM
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
            )
            data = parse_json(response.text or "")
        except Exception as exc:
            self._lg.warning(f"Discovery ranking failed: {exc}")
            return []
        raw_selected = data.get("selected")
        if not isinstance(raw_selected, list):
            return []
        ranked: list[tuple[float, ResolvedSource]] = []
        for item in raw_selected:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
                score = float(item.get("score"))
            except (TypeError, ValueError):
                continue
            if 1 <= index <= len(candidates):
                ranked.append((score, candidates[index - 1]))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [
            source
            for score, source in ranked
            if score >= settings.FEED_DISCOVERY_MIN_SCORE
        ][: settings.FEED_DISCOVERY_MAX_SOURCES]

    async def _notify(self, owner: Users, added: list[FeedSources]) -> None:
        """Report newly added sources in one message."""
        lines = ["Нашла новые источники по твоим интересам:"]
        for source in added:
            title = source.title or source.url
            lines.append(f"- {title} · {_kind_label(source.kind)}")
        try:
            await self._publisher.publish_outgoing(
                OutgoingMessage(
                    user_id=owner.user_id,
                    channel="telegram",
                    text="\n".join(lines),
                )
            )
        except Exception as exc:
            self._lg.error(f"Discovery report not sent: {exc}")


def _fact_block(facts: list[str]) -> str:
    """Format facts as prompt bullet lines."""
    return "\n".join(f"- {fact}" for fact in facts) or "- none yet"


def _collect_candidates(
    results: list[SearchResult], known_urls: set[str]
) -> list[SearchResult]:
    """Dedupe hits by host and drop hosts already in the source list."""
    taken_hosts = {_host(url) for url in known_urls}
    candidates: list[SearchResult] = []
    for result in results:
        url = result.url.strip()
        if not url.startswith(("http://", "https://")):
            continue
        if url in known_urls:
            continue
        host = _host(url)
        if not host or host in taken_hosts:
            continue
        taken_hosts.add(host)
        candidates.append(result)
        if len(candidates) >= settings.FEED_DISCOVERY_MAX_CANDIDATES:
            break
    return candidates


def _host(url: str) -> str:
    """Return the lowercase host without the www prefix."""
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _kind_label(kind: str) -> str:
    """Human-readable source kind for chat messages."""
    return "Telegram" if kind == "telegram" else "RSS"
