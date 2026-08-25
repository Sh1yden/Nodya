"""Абстракция LLM-провайдера и DTO ответов.

Ошибки разделены на transient (сеть/429/5xx — можно ретраить) и fatal
(остальные 4xx — кандидата пропускаем сразу), роутер строит поведение
на этом делении.
"""

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel

Role = Literal["dialogue", "cs", "bp", "vs"]


class LLMError(Exception):
    """База ошибок LLM-слоя."""


class LLMTransientError(LLMError):
    """Временный сбой: сеть, 429, 5xx."""


class LLMFatalError(LLMError):
    """Постоянный сбой кандидата: 4xx, неверная модель, отказ доступа."""


class ToolCall(BaseModel):
    """Вызов инструмента моделью (заполняется с Этапа 5)."""

    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """Ответ модели: текст и/или запросы инструментов."""

    text: str | None = None
    tool_calls: list[ToolCall] = []


class ChatMessage(BaseModel):
    """Сообщение в формате чата."""

    role: Literal["system", "user", "assistant"]
    content: str


class LLMProvider(ABC):
    """Единый интерфейс чат-моделей и эмбеддингов.

    tools зарезервирован для SkillRegistry (Этап 5); до тех пор
    провайдеры игнорируют его.
    """

    @abstractmethod
    async def generate(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Сгенерировать ответ чата указанной моделью."""

    @abstractmethod
    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Векторизовать тексты (VS-роль)."""
