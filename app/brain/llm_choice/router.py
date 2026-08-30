"""Role router with fallback chains (ADR-6).

Chains are configured via settings.LLM_PROVIDER_CHAINS and resolved
through ProviderRegistry. Transient candidate failure -> backoff -> next;
fatal -> next immediately. Exhausted chain raises LLMError.
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
from .registry import ProviderRegistry

_BACKOFF_BASE_SECONDS = 0.5


def _split_models(raw: str) -> list[str]:
    """Split a comma-separated model list into clean ids."""
    return [m.strip() for m in raw.split(",") if m.strip()]


class LLMRouter(LoggerMixin):
    """Route generation requests across role-specific candidate chains."""

    def __init__(self, registry: ProviderRegistry) -> None:
        """Bind provider registry used to build per-role chains.

        Args:
            registry: ProviderRegistry with registered providers.
        """
        self._registry = registry

    def _chain(self, role: Role) -> list[tuple[LLMProvider, str]]:
        """Build the ordered candidate chain for a role.

        Args:
            role: One of dialogue / cs / bp / vs.

        Returns:
            List of (provider, model_id) pairs in attempt order.
        """
        chain_config = settings.LLM_PROVIDER_CHAINS.get(role, [])
        result: list[tuple[LLMProvider, str]] = []
        for item in chain_config:
            provider_name = item["provider"]
            models = _split_models(item["models"])
            provider = self._registry.get(provider_name)
            for model in models:
                result.append((provider, model))
        return result

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
        chain = self._chain("vs")
        if not chain:
            raise LLMError("No embedding provider configured for VS role")
        provider, model = chain[0]
        return await provider.embed(texts, model)

    async def close(self) -> None:
        """Release provider HTTP resources on shutdown."""
        self._registry.close_all()
