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

    def _chain(self, role: Role) -> list[tuple[str, str]]:
        """Build the ordered candidate chain for a role (lazy resolve).

        Stores provider names, not instances, so a misconfigured
        provider does not kill the whole chain before the fallback
        loop starts.

        Args:
            role: One of dialogue / cs / bp / vs.

        Returns:
            List of (provider_name, model_id) pairs in attempt order.
        """
        chain_config = settings.LLM_PROVIDER_CHAINS.get(role, [])
        result: list[tuple[str, str]] = []
        for item in chain_config:
            provider_name = item["provider"]
            models = _split_models(item["models"])
            for model in models:
                result.append((provider_name, model))
        return result

    async def generate_with_fallback(
        self, role: Role, messages: list[ChatMessage]
    ) -> LLMResponse:
        """Walk the role chain until the first success.

        Provider resolution happens per-candidate inside the loop,
        so a single bad config entry degrades to the next fallback
        instead of aborting the whole role.

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
        for index, (provider_name, model) in enumerate(chain):
            try:
                provider = self._registry.get(provider_name)
            except (KeyError, Exception) as exc:
                # Registry import is lazy; catch KeyError and
                # ProviderDisabledError (RuntimeError subclass).
                last_error = exc
                self._lg.warning(
                    f"Candidate {provider_name}:{model} unavailable: {exc}"
                )
                continue
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
        """Embed texts via the VS role (tries candidates in order).

        Args:
            texts: Strings to vectorize.

        Returns:
            One vector per input text.

        Raises:
            LLMError: No candidate succeeded.
        """
        chain = self._chain("vs")
        if not chain:
            raise LLMError("No embedding provider configured for VS role")
        last_error: Exception | None = None
        for provider_name, model in chain:
            try:
                provider = self._registry.get(provider_name)
            except (KeyError, Exception) as exc:
                last_error = exc
                self._lg.warning(
                    f"Embedding candidate {provider_name}:{model} "
                    f"unavailable: {exc}"
                )
                continue
            try:
                return await provider.embed(texts, model)
            except LLMError as exc:
                last_error = exc
                self._lg.warning(f"Embedding candidate {model} failed: {exc}")
                continue
        raise LLMError(f"All embedding candidates failed: {last_error}")

    async def close(self) -> None:
        """Release provider HTTP resources on shutdown."""
        await self._registry.close_all()
