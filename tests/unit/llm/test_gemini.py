"""Unit tests for ``app.brain.llm_choice.gemini``."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.brain.llm_choice.base import (
    ChatMessage,
    LLMTransientError,
)
from app.brain.llm_choice.gemini import _is_transient


class TestIsTransient:
    """Test transient error classification."""

    def test_429_is_transient(self) -> None:
        exc = Mock()
        exc.status_code = 429
        assert _is_transient(exc) is True

    def test_500_is_transient(self) -> None:
        exc = Mock()
        exc.status_code = 500
        assert _is_transient(exc) is True

    def test_503_is_transient(self) -> None:
        exc = Mock()
        exc.status_code = 503
        assert _is_transient(exc) is True

    def test_400_is_fatal(self) -> None:
        exc = Mock()
        exc.status_code = 400
        assert _is_transient(exc) is False

    def test_timeout_in_class_name(self) -> None:

        class FakeTimeoutError(Exception):
            pass

        exc = FakeTimeoutError("timeout")
        assert _is_transient(exc) is True

    def test_connection_in_class_name(self) -> None:

        class FakeConnectionError(Exception):
            pass

        exc = FakeConnectionError("conn refused")
        assert _is_transient(exc) is True

    def test_unknown_exception(self) -> None:
        exc = ValueError("bad value")
        assert _is_transient(exc) is False


class TestGeminiProvider:
    """Test GeminiProvider with mocked genai client."""

    @patch("app.brain.llm_choice.gemini.genai.Client")
    async def test_generate_returns_response(self, mock_cls: Mock) -> None:
        from app.brain.llm_choice.gemini import GeminiProvider

        mock_client = AsyncMock()
        response_mock = Mock()
        response_mock.text = "Gemini response"
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=response_mock
        )
        mock_cls.return_value = mock_client

        provider = GeminiProvider("fake-key")
        messages = [
            ChatMessage(role="system", content="Be helpful"),
            ChatMessage(role="user", content="Hi"),
        ]
        response = await provider.generate(messages, "gemini-flash")
        assert response.text == "Gemini response"

    @patch("app.brain.llm_choice.gemini.genai.Client")
    async def test_generate_on_server_error_raises_transient(
        self, mock_cls: Mock
    ) -> None:
        from google.genai import errors as genai_errors

        from app.brain.llm_choice.gemini import GeminiProvider

        mock_client = AsyncMock()
        exc = genai_errors.ServerError(
            code=500, response_json={"error": "server error"}
        )
        mock_client.aio.models.generate_content.side_effect = exc
        mock_cls.return_value = mock_client

        provider = GeminiProvider("fake-key")
        with pytest.raises(LLMTransientError):
            await provider.generate(
                [ChatMessage(role="user", content="Hi")],
                "gemini-flash",
            )

    @patch("app.brain.llm_choice.gemini.genai.Client")
    async def test_embed_returns_vectors(self, mock_cls: Mock) -> None:
        from app.brain.llm_choice.gemini import GeminiProvider

        mock_client = AsyncMock()
        embedding = Mock()
        embedding.values = [0.1, 0.2, 0.3]
        embed_response = Mock()
        embed_response.embeddings = [embedding]
        mock_client.aio.models.embed_content = AsyncMock(
            return_value=embed_response
        )
        mock_cls.return_value = mock_client

        provider = GeminiProvider("fake-key")
        vectors = await provider.embed(["hello world"], "gemini-embedding")
        assert len(vectors) == 1
        assert vectors[0] == [0.1, 0.2, 0.3]
