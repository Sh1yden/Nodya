"""Consolidation — the sleep phase of Nodya (stage 7.2, D4 amendment).

When a user has been silent long enough, one CS-model call turns the
raw dialogue into:
1. durable facts  -> upsert into HardFacts + vectors into Qdrant;
2. a compressed summary -> atomically replaces the Redis context.

The raw dialogue itself is never lost: it stays in the messages
archive (ADR-14). Any failure before the context swap leaves the
history untouched — the next scan retries safely.

Triggers:
- APScheduler scan in main.py (idle >= CONSOLIDATION_IDLE_MINUTES);
- manual run: uv run python -m app.brain.memory.consolidation
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.brain.llm_choice import (
    ChatMessage,
    GeminiCloudflareProvider,
    LLMRouter,
    OpenRouterProvider,
    ProviderRegistry,
)
from app.brain.memory.long import AsyncSessionLocal
from app.brain.memory.short import ContextMessage, RedisClient
from app.brain.memory.vector import VectorMemory, VectorPoint
from app.brain.models import Users
from app.brain.repositories import HardFactsRepo
from app.core import LoggerMixin, get_logger, settings

logger = get_logger(__name__)

_ANALYSIS_SYSTEM = """You are the memory-consolidation module of Nodya,
a personal AI assistant. You receive a dialogue transcript between
the user and Nodya. Produce STRICT JSON only, no markdown fences:

{"facts": [{"category": "...", "key": "...", "value": "...",
            "confidence": 0.0}],
 "summary": "..."}

Rules for facts:
- Only durable facts about the USER (identity, preferences, work,
  relationships, goals). Ignore small talk and Nodya's replies.
- value: one concise third-person sentence in the language the user
  spoke.
- confidence in [0, 1]. Return "facts": [] when nothing is durable.

Rules for summary:
- 2-5 sentences in the user's language capturing the essence of the
  conversation: topics, decisions, unfinished threads, tone."""

_MAX_FACTS_PER_RUN = 30
_LOCK_TTL_SECONDS = 600


class ExtractedFact(BaseModel):
    """One validated fact coming back from the CS model."""

    category: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class Analysis(BaseModel):
    """Structured result of the CS consolidation call."""

    facts: list[ExtractedFact]
    summary: str = Field(min_length=1)


class ConsolidationJob(LoggerMixin):
    """Turns raw dialogues into long-term memory while users sleep."""

    def __init__(
        self,
        redis_client: RedisClient,
        session_factory: type,
        router: LLMRouter,
        vectors: VectorMemory,
    ) -> None:
        """Bind collaborators; no I/O happens here.

        Args:
            redis_client: Short-term memory (context/state/locks).
            session_factory: Async session factory for HardFacts/Users.
            router: LLM router (CS role chain).
            vectors: Qdrant-backed vector storage.
        """
        self._redis = redis_client
        self._session_factory = session_factory
        self._router = router
        self._vectors = vectors

    async def run_for_all(self) -> int:
        """Scan every user and consolidate the eligible ones.

        Eligibility: state idle (not thinking/sleeping), silent for at
        least CONSOLIDATION_IDLE_MINUTES, and enough buffered messages
        (CONSOLIDATION_MIN_MESSAGES).

        Returns:
            Number of users consolidated in this pass.
        """
        async with self._session_factory() as session:
            user_ids = (await session.scalars(select(Users.user_id))).all()
        consolidated = 0
        now = datetime.now(UTC)
        idle_seconds = settings.CONSOLIDATION_IDLE_MINUTES * 60
        for user_id in user_ids:
            state = await self._redis.get_state(user_id)
            if state is not None and state.status != "idle":
                continue
            if state is not None:
                silent_for = (now - state.last_active_at).total_seconds()
                if silent_for < idle_seconds:
                    continue
            length = await self._redis.context_length(user_id)
            if length < settings.CONSOLIDATION_MIN_MESSAGES:
                continue
            if await self.run_user(user_id):
                consolidated += 1
        return consolidated

    async def run_user(self, user_id: UUID, check_idle: bool = False) -> bool:
        """Run the sleep phase for one user under the shared lock.

        Args:
            user_id: Internal user UUID.
            check_idle: When True, require CONSOLIDATION_IDLE_MINUTES
                of silence first (used by the scheduled scan).

        Returns:
            True when consolidation completed; False when skipped or
            failed (context left untouched).

        Note:
            The lock TTL is raised to _LOCK_TTL_SECONDS because LLM
            calls may take longer than the default 30s window.
        """
        token = await self._redis.acquire_lock(user_id, ttl=_LOCK_TTL_SECONDS)
        if token is None:
            self._lg.debug(f"Consolidation skipped: lock busy {user_id}.")
            return False
        try:
            state = await self._redis.get_state(user_id)
            if check_idle and state is not None:
                silent_for = (
                    datetime.now(UTC) - state.last_active_at
                ).total_seconds()
                if silent_for < settings.CONSOLIDATION_IDLE_MINUTES * 60:
                    return False

            history = await self._redis.get_context(user_id, limit=100)
            if len(history) < settings.CONSOLIDATION_MIN_MESSAGES:
                return False

            await self._redis.set_state(user_id, "sleeping")
            try:
                analysis = await self._analyze(history)
                fact_points = await self._persist_facts(user_id, analysis)
                await self._vectorize(user_id, fact_points)

                summary_entry = ContextMessage(
                    role="summary",
                    content=analysis.summary,
                    timestamp=datetime.now(UTC),
                )
                # Atomic swap: old history dies together with the birth
                # of the summary — no empty gap for readers.
                await self._redis.replace_context(user_id, [summary_entry])
            except Exception as exc:
                # Keep the raw history on any failure; retry next tick.
                await self._redis.set_state(user_id, "idle")
                self._lg.error(f"Consolidation failed for {user_id}: {exc}")
                return False
            await self._redis.set_state(user_id, "idle")
            self._lg.info(
                f"User consolidated: user_id={user_id}, "
                f"facts={len(analysis.facts)}."
            )
            return True
        finally:
            await self._redis.release_lock(user_id, token)

    async def _analyze(self, history: list[ContextMessage]) -> Analysis:
        """Extract facts and the summary from the dialogue history.

        Args:
            history: Chronological short-term history.

        Returns:
            Parsed Analysis (facts + summary).

        Raises:
            LLMError: Every candidate of the CS chain failed.
            ValueError: Model output was not valid JSON/schema.
        """
        lines = [f"{entry.role}: {entry.content}" for entry in history]
        chat = [
            ChatMessage(role="system", content=_ANALYSIS_SYSTEM),
            ChatMessage(role="user", content="\n".join(lines)),
        ]
        response = await self._router.generate_with_fallback("cs", chat)
        payload = _parse_json(response.text or "")
        return Analysis.model_validate(payload)

    async def _persist_facts(
        self, user_id: UUID, analysis: Analysis
    ) -> list[VectorPoint]:
        """Upsert extracted facts and prepare their vector points.

        Args:
            user_id: Fact owner.
            analysis: Validated model output.

        Returns:
            Points ready for vectorization; text format matches what
            the Worker later reads back from Qdrant.
        """
        points: list[VectorPoint] = []
        async with self._session_factory() as session:
            repo = HardFactsRepo(session)
            seen: set[tuple[str, str]] = set()
            for fact in analysis.facts[:_MAX_FACTS_PER_RUN]:
                identity = (fact.category.strip(), fact.key.strip())
                if not all(identity) or identity in seen:
                    continue
                seen.add(identity)
                fact_id = await repo.upsert_fact(
                    user_id=user_id,
                    category=fact.category.strip(),
                    key=fact.key.strip(),
                    value={"text": fact.value},
                    confidence=fact.confidence,
                )
                text = f"{identity[0]} | {identity[1]}: {fact.value}"
                points.append(
                    VectorPoint(fact_id=fact_id, vector=[], text=text)
                )
            await session.commit()
        return points

    async def _vectorize(
        self, user_id: UUID, points: list[VectorPoint]
    ) -> None:
        """Embed prepared points and store them in Qdrant.

        Degraded mode: vector failure keeps PG facts safe; the same
        fact will be re-upserted (and re-vectorized) on a later run.

        Args:
            user_id: Fact owner.
            points: Points with empty vectors to fill in-place.
        """
        if not points:
            return
        try:
            vectors = await self._router.embed([p.text for p in points])
        except Exception as exc:
            self._lg.error(f"Facts not vectorized for {user_id}: {exc}")
            return
        for point, vector in zip(points, vectors, strict=True):
            point.vector = vector
        await self._vectors.upsert_points(user_id, points)


def _parse_json(raw: str) -> dict[str, Any]:
    """Parse the model answer into a JSON object defensively.

    Strips markdown fences and extracts the outermost JSON object.

    Args:
        raw: Raw model text.

    Returns:
        Parsed JSON mapping.

    Raises:
        ValueError: No valid JSON object found.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output.")
    return json.loads(cleaned[start : end + 1])


async def _cli_main() -> None:
    """Manual entry point: consolidate all eligible users.

    Usage:
        uv run python -m app.brain.memory.consolidation
    """
    redis_client = RedisClient(settings.redis_url)
    registry = ProviderRegistry(settings)
    registry.register(
        "gemini_cloudflare", GeminiCloudflareProvider, enabled=True
    )
    registry.register("openrouter", OpenRouterProvider, enabled=True)
    if settings.GEMINI_ENABLED:
        # Lazy import to keep default path free of google-genai.
        from app.brain.llm_choice.gemini import GeminiProvider

        registry.register("gemini", GeminiProvider, enabled=True)
    router = LLMRouter(registry=registry)
    vectors = VectorMemory(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        collection=settings.QDRANT_COLLECTION,
    )
    job = ConsolidationJob(redis_client, AsyncSessionLocal, router, vectors)
    count = await job.run_for_all()
    logger.info(f"Manual consolidation finished: users={count}.")
    await redis_client.close()
    await router.close()
    await vectors.close()


if __name__ == "__main__":
    asyncio.run(_cli_main())
