"""Unit tests for ``app.brain.feed.reaction``."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.brain.feed.models import FeedItem, RelevanceScore
from app.brain.feed.prompts import REACTION_INSTRUCTION
from app.brain.feed.reaction import generate_reaction
from app.brain.llm_choice import LLMResponse
from app.brain.models import Users


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


def _item() -> FeedItem:
    return FeedItem(
        entry_id="entry-1",
        title="Новая модель LLM",
        summary="Коротко о релизе",
        link="https://example.com/1",
    )


def _relevance() -> RelevanceScore:
    return RelevanceScore(score=9, summary="Прямо по интересам")


def _router(text: str = "Интересно, надо глянуть.") -> AsyncMock:
    router = AsyncMock()
    router.generate_with_fallback = AsyncMock(
        return_value=LLMResponse(text=text)
    )
    return router


class TestGenerateReaction:
    async def test_returns_dialogue_reply(self) -> None:
        router = _router("  Интересно, надо глянуть.  ")

        reaction = await generate_reaction(
            router, _owner(), _item(), _relevance()
        )

        assert reaction == "Интересно, надо глянуть."
        role, chat = router.generate_with_fallback.await_args.args
        assert role == "dialogue"
        assert REACTION_INSTRUCTION in chat[0].content
        assert "Новая модель LLM" in chat[1].content
        assert "Прямо по интересам" in chat[1].content

    async def test_disabled_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.feed.reaction.settings.FEED_REACTION_ENABLED", False
        )
        router = _router()

        reaction = await generate_reaction(
            router, _owner(), _item(), _relevance()
        )

        assert reaction is None
        router.generate_with_fallback.assert_not_awaited()

    async def test_router_failure_returns_none(self) -> None:
        router = AsyncMock()
        router.generate_with_fallback = AsyncMock(
            side_effect=RuntimeError("llm down")
        )

        assert (
            await generate_reaction(router, _owner(), _item(), _relevance())
            is None
        )

    async def test_empty_reply_returns_none(self) -> None:
        reaction = await generate_reaction(
            _router("   "), _owner(), _item(), _relevance()
        )

        assert reaction is None

    async def test_long_reply_is_truncated(self) -> None:
        reaction = await generate_reaction(
            _router("а" * 900), _owner(), _item(), _relevance()
        )

        assert reaction is not None
        assert len(reaction) == 600
