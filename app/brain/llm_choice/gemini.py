"""Google AI Studio (Gemini) via google-genai async client."""

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


def _is_transient(exc: Exception) -> bool:
    """Classify an SDK exception as transient (429/5xx/network).

    Args:
        exc: Original exception from the google-genai client.

    Returns:
        True when retrying the same candidate makes sense.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return any(word in name for word in ("timeout", "connection"))


class GeminiProvider(LLMProvider):
    """Primary provider: chat + embeddings."""

    def __init__(self, api_key: str) -> None:
        """Create the SDK client.

        Args:
            api_key: Google AI Studio API key.
        """
        self._client = genai.Client(api_key=api_key)

    async def generate(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Generate a chat reply.

        Args:
            messages: Conversation in chat format; system messages
                are folded into system_instruction.
            model: Gemini model id (e.g. gemini-3.5-flash-lite).
            tools: Ignored until SkillRegistry (stage 5).

        Returns:
            LLMResponse with reply text when present.

        Raises:
            LLMTransientError: 429/5xx or network failure.
            LLMFatalError: Other client errors (unknown model etc).
        """
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
        except Exception as exc:
            if _is_transient(exc):
                raise LLMTransientError(str(exc)) from exc
            raise LLMFatalError(str(exc)) from exc

        return LLMResponse(text=response.text or None)

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Embed texts for semantic search (VS role).

        Args:
            texts: Non-empty list of strings.
            model: Embedding model id (e.g. gemini-embedding-2).

        Returns:
            One vector per input text.

        Raises:
            LLMTransientError: 429/5xx or network failure.
            LLMFatalError: Other client errors.
        """
        try:
            result = await self._client.aio.models.embed_content(
                model=model, contents=texts
            )
        except genai_errors.ServerError as exc:
            raise LLMTransientError(str(exc)) from exc
        except Exception as exc:
            if _is_transient(exc):
                raise LLMTransientError(str(exc)) from exc
            raise LLMFatalError(str(exc)) from exc
        return [list(e.values) for e in result.embeddings]
