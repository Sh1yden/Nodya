"""Google AI Studio (Gemini) через google-genai, async-клиент."""

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from .base import (
    ChatMessage,
    LLMFatalError,
    LLMProvider,
    LLMResponse,
    LLMTransientError,
)


class GeminiProvider(LLMProvider):
    """Первичный провайдер: чат + эмбеддинги."""

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    async def generate(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            genai_types.Content(
                role="model" if m.role == "assistant" else "user",
                parts=[genai_types.Part(text=m.content)],
            )
            for m in messages
            if m.role != "system"
        ]
        config = genai_types.GenerateContentConfig(
            system_instruction=system or None,
            automatic_function_calling=(
                genai_types.AutomaticFunctionCallingConfig(disable=True)
            ),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        except genai_errors.ServerError as exc:
            raise LLMTransientError(str(exc)) from exc
        except (genai_errors.ClientError, Exception) as exc:
            if _is_transient(exc):
                raise LLMTransientError(str(exc)) from exc
            raise LLMFatalError(str(exc)) from exc

        return LLMResponse(text=response.text or None)

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        try:
            result = await self._client.aio.models.embed_content(
                model=model, contents=texts
            )
        except genai_errors.ServerError as exc:
            raise LLMTransientError(str(exc)) from exc
        except (genai_errors.ClientError, Exception) as exc:
            if _is_transient(exc):
                raise LLMTransientError(str(exc)) from exc
            raise LLMFatalError(str(exc)) from exc
        return [list(e.values) for e in result.embeddings]


def _is_transient(exc: Exception) -> bool:
    """429/5xx и сетевые сбои считаем временными."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    text = type(exc).__name__.lower()
    return any(word in text for word in ("timeout", "connection"))
