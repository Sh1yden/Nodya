"""LLM provider abstraction and response DTOs.

Errors are split into transient (network/429/5xx — retryable) and
fatal (other 4xx — skip the candidate immediately); the router builds
its behaviour on this distinction.
"""

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel

Role = Literal["dialogue", "cs", "bp", "vs", "media"]


class LLMError(Exception):
    """Base error of the LLM layer."""


class LLMTransientError(LLMError):
    """Temporary failure: network, 429, 5xx."""


class LLMFatalError(LLMError):
    """Permanent failure of a candidate: 4xx, unknown model, no access."""


class ToolCall(BaseModel):
    """A tool invocation requested by the model (used from stage 5)."""

    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """Model reply: text and/or requested tool calls."""

    text: str | None = None
    tool_calls: list[ToolCall] = []


class ChatMessage(BaseModel):
    """A chat-format conversation message."""

    role: Literal["system", "user", "assistant"]
    content: str


class LLMProvider(ABC):
    """Unified interface for chat models and embeddings.

    `tools` is reserved for SkillRegistry (stage 5); until then
    providers ignore it.
    """

    @abstractmethod
    async def generate(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Generate a chat reply with the given model."""

    @abstractmethod
    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Vectorize texts (VS role)."""
