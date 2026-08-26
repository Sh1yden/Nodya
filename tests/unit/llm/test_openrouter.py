"""Unit tests for ``app.brain.llm_choice.openrouter``."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.brain.llm_choice.base import (
    ChatMessage,
    LLMFatalError,
    LLMTransientError,
)
from app.brain.llm_choice.openrouter import OpenRouterProvider


class TestOpenRouterProviderGenerate:
    """Test generate with various HTTP scenarios."""

    @patch("app.brain.llm_choice.openrouter.httpx.AsyncClient")
    async def test_successful_response(self, mock_cls: Mock) -> None:
        client = AsyncMock()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}}]
        }
        client.post = AsyncMock(return_value=response)
        mock_cls.return_value = client

        provider = OpenRouterProvider("test-key")
        result = await provider.generate(
            [ChatMessage(role="user", content="Hi")],
            "model/test",
        )
        assert result.text == "Hello!"

    @patch("app.brain.llm_choice.openrouter.httpx.AsyncClient")
    async def test_429_raises_transient(self, mock_cls: Mock) -> None:
        client = AsyncMock()
        response = Mock()
        response.status_code = 429
        response.json.return_value = {"error": "rate limited"}
        client.post = AsyncMock(return_value=response)
        mock_cls.return_value = client

        provider = OpenRouterProvider("test-key")
        with pytest.raises(LLMTransientError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "model/test",
            )

    @patch("app.brain.llm_choice.openrouter.httpx.AsyncClient")
    async def test_500_raises_transient(self, mock_cls: Mock) -> None:
        client = AsyncMock()
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"error": "server error"}
        client.post = AsyncMock(return_value=response)
        mock_cls.return_value = client

        provider = OpenRouterProvider("test-key")
        with pytest.raises(LLMTransientError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "model/test",
            )

    @patch("app.brain.llm_choice.openrouter.httpx.AsyncClient")
    async def test_400_raises_fatal(self, mock_cls: Mock) -> None:
        client = AsyncMock()
        response = Mock()
        response.status_code = 400
        response.text = "bad request"
        response.json.return_value = {"error": "bad request"}
        client.post = AsyncMock(return_value=response)
        mock_cls.return_value = client

        provider = OpenRouterProvider("test-key")
        with pytest.raises(LLMFatalError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "model/test",
            )

    @patch("app.brain.llm_choice.openrouter.httpx.AsyncClient")
    async def test_network_error_raises_transient(
        self, mock_cls: Mock
    ) -> None:
        client = AsyncMock()
        client.post = AsyncMock(
            side_effect=httpx.NetworkError("connection refused")
        )
        mock_cls.return_value = client

        provider = OpenRouterProvider("test-key")
        with pytest.raises(LLMTransientError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "model/test",
            )


class TestOpenRouterProviderEmbed:
    @patch("app.brain.llm_choice.openrouter.httpx.AsyncClient")
    async def test_embed_always_raises_fatal(self, mock_cls: Mock) -> None:
        mock_cls.return_value = AsyncMock()
        provider = OpenRouterProvider("key")

        with pytest.raises(LLMFatalError, match="embeddings"):
            await provider.embed(["text"], "model")
