"""OpenRouter (OpenAI-compatible chat completions) via httpx.

Used as the fallback provider for all chat roles. OpenRouter offers
no embeddings API — embed() is intentionally rejected.
"""

import httpx

from .base import (
    ChatMessage,
    LLMFatalError,
    LLMProvider,
    LLMResponse,
    LLMTransientError,
)

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_TIMEOUT_SECONDS = 60


class OpenRouterProvider(LLMProvider):
    """Fallback chat provider (ADR-6)."""

    def __init__(self, api_key: str) -> None:
        """Create the shared httpx client.

        Args:
            api_key: OpenRouter API key.
        """
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)

    async def close(self) -> None:
        """Release the HTTP pool on graceful shutdown."""
        await self._client.aclose()

    async def generate(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Generate a chat reply.

        Args:
            messages: Conversation in chat format.
            model: OpenRouter model slug (e.g. nvidia/... :free).
            tools: Ignored until SkillRegistry (stage 5).

        Returns:
            LLMResponse with the assistant text when present.

        Raises:
            LLMTransientError: Network failure, 429 or 5xx.
            LLMFatalError: Other 4xx responses.
        """
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
            raise LLMFatalError(
                f"OpenRouter {model}: HTTP {response.status_code}: "
                f"{response.text[:150]}"
            )

        choice = response.json()["choices"][0]["message"]
        return LLMResponse(text=choice.get("content"))

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Not supported: OpenRouter has no embeddings API.

        Raises:
            LLMFatalError: Always — use GeminiProvider.embed instead.
        """
        raise LLMFatalError(
            "OpenRouter provides no embeddings — use GeminiProvider."
        )
