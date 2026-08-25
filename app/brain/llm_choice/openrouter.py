"""OpenRouter (OpenAI-compatible chat completions) через httpx.

Используется как fallback-провайдер для всех чат-ролей.
Эмбеддингов OpenRouter не предоставляет — embed() запрещён.
"""

import httpx

from app.core import get_logger

from .base import (
    ChatMessage,
    LLMFatalError,
    LLMProvider,
    LLMResponse,
    LLMTransientError,
)

logger = get_logger(__name__)

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT_SECONDS = 60


class OpenRouterProvider(LLMProvider):
    """Резервный провайдер чата (ADR-6)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def close(self) -> None:
        """Освободить HTTP-пул при graceful shutdown."""
        await self._client.aclose()

    async def generate(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        payload = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
        }
        try:
            response = await self._client.post(
                _API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            raise LLMTransientError(str(exc)) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise LLMTransientError(
                f"OpenRouter {model}: HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            detail = response.text[:150]
            raise LLMFatalError(
                f"OpenRouter {model}: HTTP {response.status_code}: {detail}"
            )

        choice = response.json()["choices"][0]["message"]
        return LLMResponse(text=choice.get("content"))

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        raise LLMFatalError(
            "OpenRouter не предоставляет embeddings — используйте Gemini."
        )
