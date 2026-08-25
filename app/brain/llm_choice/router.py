"""Role router with fallback chains (ADR-6, matrix from tldr notes).

A role chain = Gemini candidates (if any) + the OpenRouter fallback
pair. Transient candidate failure -> backoff pause -> next; fatal ->
next immediately. Exhausted chain raises LLMError up to the Worker,
which NACKs the batch into DLQ.
"""

import asyncio

from app.core import LoggerMixin, settings

from .base import (
    ChatMessage,
    LLMError,
    LLMProvider,
    LLMResponse,
    LLMTransientError,
    Role,
)
from .gemini import GeminiProvider
from .openrouter import OpenRouterProvider

_BACKOFF_BASE_SECONDS = 0.5


def _split_models(raw: str) -> list[str]:
    """Split a comma-separated model list into clean ids.

    Args:
        raw: Raw setting value.

    Returns:
        Non-empty stripped model ids in order.
    """
    return [m.strip() for m in raw.split(",") if m.strip()]


class LLMRouter(LoggerMixin):
    """Route generation requests across role-specific candidates."""

    def __init__(
        self,
        gemini: GeminiProvider,
        openrouter: OpenRouterProvider,
    ) -> None:
        """Bind providers used to build per-role chains.

        Args:
            gemini: Primary provider (chat + embeddings).
            openrouter: Fallback chat provider.
        """
        self._gemini = gemini
        self._openrouter = openrouter

    def _chain(self, role: Role) -> list[tuple[LLMProvider, str]]:
        """Build the ordered candidate chain for a role.

        Args:
            role: One of dialogue / cs / bp / vs.

        Returns:
            List of (provider, model_id) pairs in attempt order.
        """
        fallback = [
            (self._openrouter, model)
            for model in _split_models(settings.LLM_FALLBACK_OPENROUTER)
        ]
        if role == "dialogue":
            gemini_part = [
                (self._gemini, model)
                for model in _split_models(settings.LLM_DIALOGUE_GEMINI)
            ]
            return gemini_part + fallback
        if role == "cs":
            gemini_part = [
                (self._gemini, model)
                for model in _split_models(settings.LLM_CS_GEMINI)
            ]
            return gemini_part + fallback
        if role == "bp":
            bp = [
                (self._openrouter, model)
                for model in _split_models(settings.LLM_BP_OPENROUTER)
            ]
            return bp + fallback
        # VS: embeddings exist only at Gemini
        return [(self._gemini, settings.LLM_EMBED_MODEL)]

    async def generate_with_fallback(
        self, role: Role, messages: list[ChatMessage]
    ) -> LLMResponse:
        """Walk the role chain until the first success.

        Args:
            role: Target role determining the candidate chain.
            messages: Conversation in chat format.

        Returns:
            The first successful LLMResponse.

        Raises:
            LLMError: Every candidate failed; carries the last error.
        """
        chain = self._chain(role)
        last_error: Exception | None = None
        for index, (provider, model) in enumerate(chain):
            try:
                response = await provider.generate(messages, model)
                if index > 0:
                    self._lg.warning(
                        f"Role '{role}' served by fallback: {model}."
                    )
                return response
            except LLMTransientError as exc:
                last_error = exc
                delay = _BACKOFF_BASE_SECONDS * (2**index)
                self._lg.warning(
                    f"Candidate {model} transient failure ({exc}); "
                    f"pausing {delay:.1f}s."
                )
                await asyncio.sleep(delay)
            except LLMError as exc:
                last_error = exc
                self._lg.warning(f"Candidate {model} rejected: {exc}")
        raise LLMError(
            f"All candidates of role '{role}' exhausted: {last_error}"
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts via the VS role (no OpenRouter fallback).

        Args:
            texts: Strings to vectorize.

        Returns:
            One vector per input text.
        """
        provider, model = self._chain("vs")[0]
        return await provider.embed(texts, model)

    async def close(self) -> None:
        """Release provider HTTP resources on shutdown."""
        await self._openrouter.close()
