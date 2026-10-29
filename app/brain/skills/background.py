"""Background feed parser: relevance -> proactive messages (7.1/7.4).

Runs from APScheduler in the Gateway process. For every active feed
source (RSS, Atom or a public Telegram channel) it fetches new
entries, asks the BP-role model how relevant each entry is for the
owner, then:

- score >= FEED_NOTIFY_SCORE -> OutgoingMessage to the owner with a
  short in-persona reaction when the daily quota allows it;
- FEED_STORE_SCORE <= score < notify -> summary into Qdrant memory;
- below the store threshold -> dropped.

Entries are claimed in Redis (SET NX EX) before scoring, so repeated
feed reads never cost another model call or produce duplicates. The
owner is the single account with role="owner"; without one the scan
is a no-op. FEED_MAX_ITEMS_PER_RUN caps model calls per scan.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.api.messaging import MessagePublisher
from app.brain.feed.fetchers import fetch_source
from app.brain.feed.models import FeedItem, RelevanceScore
from app.brain.feed.profile import load_owner_fact_lines
from app.brain.feed.prompts import SCORING_SYSTEM
from app.brain.feed.reaction import generate_reaction
from app.brain.feed.text import clean_text as _clean_text
from app.brain.feed.text import parse_json as _parse_json
from app.brain.llm_choice import ChatMessage, LLMRouter
from app.brain.memory.short import RedisClient
from app.brain.memory.vector import VectorMemory, VectorPoint
from app.brain.models import FeedSources, Users
from app.brain.repositories import FeedSourcesRepo
from app.common import OutgoingMessage
from app.core import LoggerMixin, settings

__all__ = [
    "BackgroundParserJob",
    "FeedItem",
    "RelevanceScore",
    "_clean_text",
    "_digest",
    "_parse_json",
    "_point_id",
]


class BackgroundParserJob(LoggerMixin):
    """Polls feed sources and turns relevant entries into actions."""

    def __init__(
        self,
        redis_client: RedisClient,
        session_factory: type,
        router: LLMRouter,
        vectors: VectorMemory,
        publisher: MessagePublisher,
    ) -> None:
        """Bind collaborators; no I/O happens here.

        Args:
            redis_client: Dedupe claims and notification quotas.
            session_factory: Async session factory (feeds, owner,
                facts).
            router: LLM router (BP role for scoring, VS for embed).
            vectors: Qdrant-backed long-term memory.
            publisher: Outgoing publisher for proactive messages.
        """
        self._redis = redis_client
        self._session_factory = session_factory
        self._router = router
        self._vectors = vectors
        self._publisher = publisher

    async def run(self) -> int:
        """Poll every due source once within the per-run budget.

        Returns:
            Number of new entries processed in this pass.
        """
        owner = await self._load_owner()
        if owner is None:
            self._lg.warning("Feed scan skipped: no owner account.")
            return 0
        async with self._session_factory() as session:
            sources = await FeedSourcesRepo(session).list_active()
        processed = 0
        budget = settings.FEED_MAX_ITEMS_PER_RUN
        now = datetime.now(UTC)
        for source in sources:
            if budget <= 0:
                self._lg.debug("Feed scan budget exhausted.")
                break
            if not self._is_due(source, now):
                continue
            items = await self._fetch(source)
            if items is None:
                continue
            exhausted = False
            for item in items:
                if budget <= 0:
                    exhausted = True
                    break
                if await self._handle_item(owner, source, item):
                    budget -= 1
                    processed += 1
            if exhausted:
                # The source is only partially handled: keep its
                # timestamp so the rest is retried on the next scan.
                break
            async with self._session_factory() as session:
                await FeedSourcesRepo(session).touch(source.source_id)
                await session.commit()
        if processed:
            self._lg.info(f"Feed scan finished: entries={processed}.")
        return processed

    @staticmethod
    def _is_due(source: FeedSources, now: datetime) -> bool:
        """Whether a source may be polled in this scan.

        Args:
            source: Feed source row.
            now: Current UTC time.

        Returns:
            True when the source was never polled or the poll
            window has elapsed.
        """
        if source.last_checked_at is None:
            return True
        elapsed = now - source.last_checked_at
        return elapsed >= timedelta(minutes=settings.FEED_SCAN_MINUTES)

    async def _load_owner(self) -> Users | None:
        """Fetch the single owner account.

        Returns:
            The owner row, or None when bootstrap has not run.
        """
        async with self._session_factory() as session:
            result = await session.scalars(
                select(Users).where(Users.role == "owner")
            )
            return result.first()

    async def _fetch(self, source: FeedSources) -> list[FeedItem] | None:
        """Download and normalize entries of one source.

        Args:
            source: Feed source to poll.

        Returns:
            Normalized entries, or None on failure.
        """
        return await fetch_source(source.kind, source.url)

    async def _handle_item(
        self, owner: Users, source: FeedSources, item: FeedItem
    ) -> bool:
        """Score one entry and act on the verdict.

        The claim is taken before scoring on purpose: an entry that
        already failed once must not burn another model call on
        every scan.

        Args:
            owner: Account that receives proactive messages.
            source: Source the entry belongs to.
            item: Normalized feed entry.

        Returns:
            True when the entry was newly processed.
        """
        digest = _digest(source.source_id, item.entry_id)
        ttl = settings.FEED_SEEN_TTL_DAYS * 24 * 60 * 60
        if not await self._redis.claim_feed_item(digest, ttl):
            return False
        relevance = await self._filter_relevance(owner, item)
        if relevance is None:
            return True
        if relevance.score >= settings.FEED_NOTIFY_SCORE:
            if not await self._notify(owner, item, relevance):
                await self._remember(owner, source, item, relevance)
        elif relevance.score >= settings.FEED_STORE_SCORE:
            await self._remember(owner, source, item, relevance)
        return True

    async def _filter_relevance(
        self, owner: Users, item: FeedItem
    ) -> RelevanceScore | None:
        """Ask the BP-role model how relevant an entry is.

        Args:
            owner: Person the relevance is judged for.
            item: Feed entry to score.

        Returns:
            Validated verdict, or None when the model failed or
            returned garbage (the entry is then dropped).
        """
        facts = await load_owner_fact_lines(
            self._session_factory, owner.user_id
        )
        fact_lines = "\n".join(f"- {line}" for line in facts)
        prompt = (
            f"Person: {owner.display_name or owner.username}\n"
            "Known facts:\n"
            f"{fact_lines or '- none yet'}\n\n"
            "News item:\n"
            f"Title: {item.title}\n"
            f"Summary: {item.summary or '(no summary)'}\n"
            f"Link: {item.link or '(no link)'}"
        )
        try:
            response = await self._router.generate_with_fallback(
                "bp",
                [
                    ChatMessage(role="system", content=SCORING_SYSTEM),
                    ChatMessage(role="user", content=prompt),
                ],
            )
        except Exception as exc:
            self._lg.error(f"Relevance scoring failed: {exc}")
            return None
        try:
            return RelevanceScore.model_validate(
                _parse_json(response.text or "")
            )
        except (ValueError, ValidationError) as exc:
            self._lg.warning(f"Bad relevance verdict: {exc}")
            return None

    async def _notify(
        self, owner: Users, item: FeedItem, relevance: RelevanceScore
    ) -> bool:
        """Send a proactive message about a highly relevant entry.

        Respects the daily notification quota: when it is exhausted
        the method returns False and the caller stores the item as a
        digest instead of pinging the owner.

        Args:
            owner: Recipient of the message.
            item: Source entry.
            relevance: Model verdict with the explanation.

        Returns:
            True when the message was published.
        """
        allowed = await self._redis.consume_daily_quota(
            f"feed_notify:{owner.user_id}",
            settings.FEED_NOTIFY_DAILY_LIMIT,
        )
        if not allowed:
            self._lg.info(
                f"Feed notify quota exhausted: user_id={owner.user_id}."
            )
            return False
        reaction = await generate_reaction(
            self._router, owner, item, relevance
        )
        text = (
            f"Нашёл кое-что по твоим интересам "
            f"(релевантность {relevance.score:g}/10):\n"
            f"{item.title}\n{relevance.summary}"
        )
        if item.link:
            text = f"{text}\n{item.link}"
        if reaction:
            text = f"{text}\n\n{reaction}"
        await self._publisher.publish_outgoing(
            OutgoingMessage(
                user_id=owner.user_id,
                channel="telegram",
                text=text,
            )
        )
        self._lg.info(
            f"Feed notify: user_id={owner.user_id} "
            f"score={relevance.score:g} title={item.title!r}."
        )
        return True

    async def _remember(
        self,
        owner: Users,
        source: FeedSources,
        item: FeedItem,
        relevance: RelevanceScore,
    ) -> None:
        """Store a medium-relevance digest in long-term memory.

        Degraded mode: embeddings/Qdrant failures log ERROR and do
        not abort the scan.

        Args:
            owner: Memory owner.
            source: Source the entry belongs to.
            item: Source entry.
            relevance: Model verdict with the summary.
        """
        text = f"news | {item.title}: {relevance.summary}"
        try:
            vector = (await self._router.embed([text]))[0]
            await self._vectors.upsert_points(
                owner.user_id,
                [
                    VectorPoint(
                        fact_id=_point_id(
                            str(owner.user_id),
                            source.source_id,
                            item.entry_id,
                        ),
                        vector=vector,
                        text=text,
                    )
                ],
            )
        except Exception as exc:
            self._lg.error(f"Feed digest not stored: {exc}")


def _digest(source_id: UUID, entry_id: str) -> str:
    """Stable dedupe key of one feed entry.

    Args:
        source_id: Source UUID.
        entry_id: Entry id/link/title from the feed.

    Returns:
        sha256 hex digest.
    """
    return hashlib.sha256(f"{source_id}:{entry_id}".encode()).hexdigest()


def _point_id(owner_id: str, source_id: UUID, entry_id: str) -> int:
    """Deterministic Qdrant point id of a digest.

    Args:
        owner_id: Memory owner UUID as text.
        source_id: Source UUID.
        entry_id: Entry id/link/title from the feed.

    Returns:
        Positive 63-bit integer safe for Qdrant.
    """
    digest = hashlib.sha256(
        f"{owner_id}:{source_id}:{entry_id}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big") % (2**63)
