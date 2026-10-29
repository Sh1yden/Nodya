"""Owner interest profile shared by scoring and discovery."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.brain.repositories import HardFactsRepo
from app.core import get_logger, settings

logger = get_logger(__name__)


async def load_owner_fact_lines(
    session_factory: Any, user_id: UUID, limit: int | None = None
) -> list[str]:
    """Load the owner facts used as the interest profile.

    Degraded mode: a DB failure yields an empty profile instead of
    aborting the caller.

    Args:
        session_factory: Async session factory for DB access.
        user_id: Owner UUID.
        limit: Max facts; defaults to FACTS_IN_PROMPT_LIMIT.

    Returns:
        Human-readable fact lines.
    """
    try:
        async with session_factory() as session:
            facts = await HardFactsRepo(session).search_last_updated(
                user_id,
                limit=limit
                if limit is not None
                else settings.FACTS_IN_PROMPT_LIMIT,
            )
    except Exception as exc:
        logger.error(f"Feed interest profile unavailable: {exc}")
        return []
    return [
        f"{f.category} | {f.key}: {f.value.get('text', '')}"
        for f in facts
        if f.value.get("text")
    ]
