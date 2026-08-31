"""Gemini via Cloudflare Worker (OpenAI-compatible API) using httpx.

The worker at GEMINI_CLOUDFLARE_URL exposes:
- POST /chat/completions  (OpenAI chat completions format)
- POST /embeddings        (OpenAI embeddings format)
- GET  /models            (OpenAI models list format)

Auth: Authorization: Bearer <GEMINI_API_KEY>
"""

import json

import httpx

from app.core.config import SettingsSchema

from .base import (
    ChatMessage,
    LLMFatalError,
    LLMProvider,
    LLMResponse,
    LLMTransientError,
)

_API_TIMEOUT = 60.0


def _is_transient(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


class GeminiCloudflareProvider(LLMProvider):
    """Gemini provider via Cloudflare Worker proxy.

    Uses OpenAI-compatible REST API (non-streaming).
    """

    def __init__(self, settings: SettingsSchema) -> None:
        base_url = settings.GEMINI_CLOUDFLARE_URL.rstrip("/")
        self._chat_url = f"{base_url}/chat/completions"
        self._embed_url = f"{base_url}/embeddings"
        self._api_key = settings.GEMINI_API_KEY
        self._client = httpx.AsyncClient(timeout=_API_TIMEOUT)

    async def close(self) -> None:
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
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        try:
            response = await self._client.post(
                self._chat_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise LLMTransientError(f"Network error: {exc}") from exc

        if _is_transient(response.status_code):
            raise LLMTransientError(
                f"Gemini Cloudflare {model}: HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise LLMFatalError(
                f"Gemini Cloudflare {model}: HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )

        data = response.json()
        choice = data["choices"][0]["message"]
        tool_calls = []
        for tc in choice.get("tool_calls") or []:
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(
                {"name": tc["function"]["name"], "arguments": args}
            )
        return LLMResponse(
            text=choice.get("content"),
            tool_calls=tool_calls,
        )

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        payload = {
            "model": model,
            "input": texts,
        }
        try:
            response = await self._client.post(
                self._embed_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise LLMTransientError(f"Network error: {exc}") from exc

        if _is_transient(response.status_code):
            raise LLMTransientError(
                f"Gemini Cloudflare embed {model}: HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise LLMFatalError(
                f"Gemini Cloudflare embed {model}: HTTP "
                f"{response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        return [item["embedding"] for item in data["data"]]
