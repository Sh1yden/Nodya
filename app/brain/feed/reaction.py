"""Nodya's personal reaction attached to feed notifications."""

from __future__ import annotations

from app.brain.feed.models import FeedItem, RelevanceScore
from app.brain.feed.prompts import REACTION_INSTRUCTION
from app.brain.llm_choice import ChatMessage, LLMRouter
from app.brain.memory.init.prompts import load_system_prompt
from app.brain.models import Users
from app.core import get_logger, settings

logger = get_logger(__name__)

_REACTION_LIMIT = 600


async def generate_reaction(
    router: LLMRouter,
    owner: Users,
    item: FeedItem,
    relevance: RelevanceScore,
) -> str | None:
    """Write a short in-persona comment about a news item.

    Degraded mode: any failure returns None so the notification is
    still delivered without a reaction.

    Args:
        router: LLM router (dialogue role carries the persona).
        owner: Recipient of the notification.
        item: News entry being delivered.
        relevance: Model verdict with the explanation.

    Returns:
        Reaction text, or None when disabled or failed.
    """
    if not settings.FEED_REACTION_ENABLED:
        return None
    system = f"{load_system_prompt(is_owner=True)}\n\n{REACTION_INSTRUCTION}"
    prompt = (
        f"Владелец: {owner.display_name or owner.username}\n"
        f"Новость: {item.title}\n"
        f"Суть: {relevance.summary}\n"
        f"Ссылка: {item.link or '—'}"
    )
    try:
        response = await router.generate_with_fallback(
            "dialogue",
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=prompt),
            ],
        )
    except Exception as exc:
        logger.warning(f"Feed reaction failed: {exc}")
        return None
    text = (response.text or "").strip()
    if not text:
        return None
    return text[:_REACTION_LIMIT]
