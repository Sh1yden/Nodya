"""Роутер ролей с fallback-цепочками (ADR-6, матрица tldr).

Цепочка роли = кандидаты Gemini (если есть) + OpenRouter-fallback.
Transient-ошибка кандидата -> пауза -> следующий; fatal -> сразу
следующий. Исчерпание цепочки -> LLMError наверх (Worker NACK'ает
пачку в DLQ).
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
    return [m.strip() for m in raw.split(",") if m.strip()]


class LLMRouter(LoggerMixin):
    """Маршрутизатор по ролям D/CS/BP/VS."""

    def __init__(
        self,
        gemini: GeminiProvider,
        openrouter: OpenRouterProvider,
    ) -> None:
        self._gemini = gemini
        self._openrouter = openrouter

    def _chain(self, role: Role) -> list[tuple[LLMProvider, str]]:
        fallback = [
            (self._openrouter, model)
            for model in _split_models(settings.LLM_FALLBACK_OPENROUTER)
        ]
        if role == "dialogue":
            return [
                (self._gemini, model)
                for model in _split_models(settings.LLM_DIALOGUE_GEMINI)
            ] + fallback
        if role == "cs":
            return [
                (self._gemini, model)
                for model in _split_models(settings.LLM_CS_GEMINI)
            ] + fallback
        if role == "bp":
            return [
                (self._openrouter, model)
                for model in _split_models(settings.LLM_BP_OPENROUTER)
            ] + fallback
        return [(self._gemini, settings.LLM_EMBED_MODEL)]

    async def generate_with_fallback(
        self, role: Role, messages: list[ChatMessage]
    ) -> LLMResponse:
        """Пройти цепочку роли до первого успеха."""
        chain = self._chain(role)
        last_error: Exception | None = None
        for index, (provider, model) in enumerate(chain):
            try:
                response = await provider.generate(messages, model)
                if index > 0:
                    self._lg.warning(
                        "Роль %s обслужена fallback'ом: %s.",
                        role,
                        model,
                    )
                return response
            except LLMTransientError as exc:
                last_error = exc
                delay = _BACKOFF_BASE_SECONDS * (2**index)
                self._lg.warning(
                    "Кандидат %s упал transient (%s); ждём %.1fs.",
                    model,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except LLMError as exc:
                last_error = exc
                self._lg.warning("Кандидат %s отклонён: %s.", model, exc)
        raise LLMError(f"Все кандидаты роли '{role}' исчерпаны: {last_error}")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """VS-роль без OpenRouter-fallback (embeddings только у Gemini)."""
        provider, model = self._chain("vs")[0]
        return await provider.embed(texts, model)

    async def close(self) -> None:
        """Закрыть HTTP-ресурсы провайдеров при shutdown."""
        await self._openrouter.close()
