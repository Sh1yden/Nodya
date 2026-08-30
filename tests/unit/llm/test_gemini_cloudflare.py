"""Unit tests for ``app.brain.llm_choice.gemini_from_cloudflare``."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.brain.llm_choice.base import (
    ChatMessage,
    LLMFatalError,
    LLMTransientError,
)
from app.brain.llm_choice.gemini_from_cloudflare import (
    GeminiCloudflareProvider,
)
from app.core.config import SettingsSchema


def _make_settings(**overrides) -> SettingsSchema:
    settings = SettingsSchema(
        GEMINI_API_KEY="test-key",
        GEMINI_CLOUDFLARE_URL="https://test-worker.example.com/",
        **overrides,
    )
    return settings


class TestGeminiCloudflareProvider:
    """Test GeminiCloudflareProvider with mocked httpx client."""

    @patch("app.brain.llm_choice.gemini_from_cloudflare.httpx.AsyncClient")
    async def test_generate_returns_response(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Gemini response", "tool_calls": None}}
            ]
        }
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        messages = [
            ChatMessage(role="system", content="Be helpful"),
            ChatMessage(role="user", content="Hi"),
        ]
        response = await provider.generate(messages, "gemini-flash")
        assert response.text == "Gemini response"
        assert response.tool_calls == []

    @patch("app.brain.llm_choice.gemini_from_cloudflare.httpx.AsyncClient")
    async def test_generate_with_tool_calls(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"city": "Moscow"}',
                                }
                            }
                        ],
                    }
                }
            ]
        }
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        response = await provider.generate(
            [ChatMessage(role="user", content="Weather?")],
            "gemini-flash",
            tools=[{"type": "function", "function": {"name": "get_weather"}}],
        )
        assert response.text is None
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "get_weather"
        assert response.tool_calls[0].arguments == {"city": "Moscow"}

    @patch("app.brain.llm_choice.gemini_from_cloudflare.httpx.AsyncClient")
    async def test_generate_on_429_raises_transient(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        with pytest.raises(LLMTransientError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "gemini-flash",
            )

    @patch("app.brain.llm_choice.gemini_from_cloudflare.httpx.AsyncClient")
    async def test_generate_on_500_raises_transient(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal error"
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        with pytest.raises(LLMTransientError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "gemini-flash",
            )

    @patch("app.brain.llm_choice.gemini_from_cloudflare.httpx.AsyncClient")
    async def test_generate_on_400_raises_fatal(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = "Bad request"
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        with pytest.raises(LLMFatalError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "gemini-flash",
            )

    @patch("app.brain.llm_choice.gemini_from_cloudflare.httpx.AsyncClient")
    async def test_generate_on_network_error_raises_transient(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection failed")
        )
        mock_client_cls.return_value = mock_client

        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        with pytest.raises(LLMTransientError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "gemini-flash",
            )

    @patch("app.brain.llm_choice.gemini_from_cloudflare.httpx.AsyncClient")
    async def test_embed_returns_vectors(self, mock_client_cls: Mock) -> None:
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        }
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        vectors = await provider.embed(
            ["hello world", "foo bar"], "gemini-embedding-2"
        )
        assert len(vectors) == 2
        assert vectors[0] == [0.1, 0.2, 0.3]
        assert vectors[1] == [0.4, 0.5, 0.6]

    @patch("app.brain.llm_choice.gemini_from_cloudflare.httpx.AsyncClient")
    async def test_embed_on_error_raises_transient(
        self, mock_client_cls: Mock
    ) -> None:
        mock_client = AsyncMock()
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        with pytest.raises(LLMTransientError):
            await provider.embed(["hello"], "gemini-embedding-2")

    async def test_close_calls_aclose(self) -> None:
        settings = _make_settings()
        provider = GeminiCloudflareProvider(settings)
        provider._client.aclose = AsyncMock()
        await provider.close()
        provider._client.aclose.assert_awaited_once()
