"""Unit tests for ``app.brain.llm_choice.router``."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.brain.llm_choice.base import (
    ChatMessage,
    LLMError,
    LLMFatalError,
    LLMProvider,
    LLMResponse,
    LLMTransientError,
)
from app.brain.llm_choice.registry import ProviderRegistry
from app.brain.llm_choice.router import LLMRouter, _split_models


class TestSplitModels:
    """Test comma-separated model string parsing."""

    def test_single_model(self) -> None:
        assert _split_models("gemini-flash") == ["gemini-flash"]

    def test_multiple_models(self) -> None:
        result = _split_models("model-a, model-b, model-c")
        assert result == ["model-a", "model-b", "model-c"]

    def test_whitespace_stripped(self) -> None:
        result = _split_models("  a ,  b  ")
        assert result == ["a", "b"]

    def test_empty_string(self) -> None:
        assert _split_models("") == []

    def test_trailing_comma(self) -> None:
        result = _split_models("a,b,")
        assert result == ["a", "b"]


def _make_mock_registry(
    gemini: LLMProvider | None = None, openrouter: LLMProvider | None = None
) -> ProviderRegistry:
    registry = Mock(spec=ProviderRegistry)
    providers = {}
    if gemini:
        providers["gemini_cloudflare"] = gemini
    if openrouter:
        providers["openrouter"] = openrouter

    def get_provider(name: str) -> LLMProvider:
        if name not in providers:
            raise KeyError(f"Provider '{name}' not registered")
        return providers[name]

    registry.get.side_effect = get_provider
    return registry


class TestLLMRouterChain:
    """Test chain construction for different roles."""

    @patch("app.brain.llm_choice.router.settings")
    def test_dialogue_chain(self, mock_settings: Mock) -> None:
        mock_settings.LLM_PROVIDER_CHAINS = {
            "dialogue": [
                {"provider": "gemini_cloudflare", "models": "gemini-flash"},
                {"provider": "openrouter", "models": "nemotron-free"},
            ],
            "cs": [],
            "bp": [],
            "vs": [
                {"provider": "gemini_cloudflare", "models": "gemini-embed"}
            ],
        }

        gemini = AsyncMock()
        openrouter = AsyncMock()
        registry = _make_mock_registry(gemini, openrouter)
        router = LLMRouter(registry=registry)

        chain = router._chain("dialogue")
        assert len(chain) == 2
        assert chain[0][0] == "gemini_cloudflare"
        assert chain[0][1] == "gemini-flash"
        assert chain[1][0] == "openrouter"
        assert chain[1][1] == "nemotron-free"

    @patch("app.brain.llm_choice.router.settings")
    def test_bp_chain(self, mock_settings: Mock) -> None:
        mock_settings.LLM_PROVIDER_CHAINS = {
            "dialogue": [],
            "cs": [],
            "bp": [
                {"provider": "openrouter", "models": "gemma-free"},
                {"provider": "openrouter", "models": "nemotron-free"},
            ],
            "vs": [],
        }

        gemini = AsyncMock()
        openrouter = AsyncMock()
        registry = _make_mock_registry(gemini, openrouter)
        router = LLMRouter(registry=registry)

        chain = router._chain("bp")
        assert len(chain) == 2
        assert chain[0][0] == "openrouter"
        assert chain[0][1] == "gemma-free"
        assert chain[1][0] == "openrouter"
        assert chain[1][1] == "nemotron-free"

    @patch("app.brain.llm_choice.router.settings")
    def test_vs_chain_single_gemini(self, mock_settings: Mock) -> None:
        mock_settings.LLM_PROVIDER_CHAINS = {
            "vs": [
                {"provider": "gemini_cloudflare", "models": "gemini-embed"}
            ],
        }

        gemini = AsyncMock()
        openrouter = AsyncMock()
        registry = _make_mock_registry(gemini, openrouter)
        router = LLMRouter(registry=registry)

        chain = router._chain("vs")
        assert len(chain) == 1
        assert chain[0][0] == "gemini_cloudflare"
        assert chain[0][1] == "gemini-embed"


class TestLLMRouterGenerateWithFallback:
    @patch("app.brain.llm_choice.router.settings")
    @patch(
        "app.brain.llm_choice.router.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_success_on_first_candidate(
        self, mock_sleep: AsyncMock, mock_settings: Mock
    ) -> None:
        mock_settings.LLM_PROVIDER_CHAINS = {
            "dialogue": [
                {"provider": "gemini_cloudflare", "models": "gemini-flash"},
                {"provider": "openrouter", "models": "nemotron-free"},
            ],
            "cs": [],
            "bp": [],
            "vs": [
                {"provider": "gemini_cloudflare", "models": "gemini-embed"}
            ],
        }

        gemini = AsyncMock()
        gemini.generate = AsyncMock(return_value=LLMResponse(text="ok"))
        openrouter = AsyncMock()
        registry = _make_mock_registry(gemini, openrouter)
        router = LLMRouter(registry=registry)

        result = await router.generate_with_fallback(
            "dialogue",
            [ChatMessage(role="user", content="Hi")],
        )
        assert result.text == "ok"

    @patch("app.brain.llm_choice.router.settings")
    @patch(
        "app.brain.llm_choice.router.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_fallback_on_transient_error(
        self, mock_sleep: AsyncMock, mock_settings: Mock
    ) -> None:
        mock_settings.LLM_PROVIDER_CHAINS = {
            "dialogue": [
                {"provider": "gemini_cloudflare", "models": "gemini-flash"},
                {"provider": "openrouter", "models": "nemotron-free"},
            ],
            "cs": [],
            "bp": [],
            "vs": [
                {"provider": "gemini_cloudflare", "models": "gemini-embed"}
            ],
        }

        gemini = AsyncMock()
        gemini.generate = AsyncMock(side_effect=LLMTransientError("429"))
        openrouter = AsyncMock()
        openrouter.generate = AsyncMock(
            return_value=LLMResponse(text="fallback-ok")
        )
        registry = _make_mock_registry(gemini, openrouter)
        router = LLMRouter(registry=registry)

        result = await router.generate_with_fallback(
            "dialogue",
            [ChatMessage(role="user", content="Hi")],
        )
        assert result.text == "fallback-ok"
        assert mock_sleep.awaited

    @patch("app.brain.llm_choice.router.settings")
    @patch(
        "app.brain.llm_choice.router.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_all_fail_raises_llm_error(
        self, mock_sleep: AsyncMock, mock_settings: Mock
    ) -> None:
        mock_settings.LLM_PROVIDER_CHAINS = {
            "dialogue": [
                {"provider": "gemini_cloudflare", "models": "gemini-flash"},
                {"provider": "openrouter", "models": "nemotron-free"},
            ],
            "cs": [],
            "bp": [],
            "vs": [
                {"provider": "gemini_cloudflare", "models": "gemini-embed"}
            ],
        }

        gemini = AsyncMock()
        gemini.generate = AsyncMock(side_effect=LLMTransientError("down"))
        openrouter = AsyncMock()
        openrouter.generate = AsyncMock(
            side_effect=LLMTransientError("also down")
        )
        registry = _make_mock_registry(gemini, openrouter)
        router = LLMRouter(registry=registry)

        with pytest.raises(LLMError):
            await router.generate_with_fallback(
                "dialogue",
                [ChatMessage(role="user", content="Hi")],
            )

    @patch("app.brain.llm_choice.router.settings")
    @patch(
        "app.brain.llm_choice.router.asyncio.sleep",
        new_callable=AsyncMock,
    )
    async def test_fatal_error_skips_to_next(
        self, mock_sleep: AsyncMock, mock_settings: Mock
    ) -> None:
        mock_settings.LLM_PROVIDER_CHAINS = {
            "dialogue": [
                {"provider": "gemini_cloudflare", "models": "gemini-flash"},
                {"provider": "openrouter", "models": "nemotron-free"},
            ],
            "cs": [],
            "bp": [],
            "vs": [
                {"provider": "gemini_cloudflare", "models": "gemini-embed"}
            ],
        }

        gemini = AsyncMock()
        gemini.generate = AsyncMock(side_effect=LLMFatalError("bad model"))
        openrouter = AsyncMock()
        openrouter.generate = AsyncMock(
            return_value=LLMResponse(text="after-fatal")
        )
        registry = _make_mock_registry(gemini, openrouter)
        router = LLMRouter(registry=registry)

        result = await router.generate_with_fallback(
            "dialogue",
            [ChatMessage(role="user", content="Hi")],
        )
        assert result.text == "after-fatal"
        mock_sleep.assert_not_awaited()


class TestLLMRouterEmbed:
    @patch("app.brain.llm_choice.router.settings")
    async def test_embed_delegates_to_gemini(
        self, mock_settings: Mock
    ) -> None:
        mock_settings.LLM_PROVIDER_CHAINS = {
            "vs": [
                {"provider": "gemini_cloudflare", "models": "gemini-embed"}
            ],
        }

        gemini = AsyncMock()
        gemini.embed = AsyncMock(return_value=[[0.1, 0.2]])
        openrouter = AsyncMock()
        registry = _make_mock_registry(gemini, openrouter)
        router = LLMRouter(registry=registry)

        result = await router.embed(["hello"])
        assert result == [[0.1, 0.2]]
        gemini.embed.assert_awaited_once_with(["hello"], "gemini-embed")
