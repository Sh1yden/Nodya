"""Unit tests for ``app.brain.llm_choice.base``."""

from __future__ import annotations

import pytest

from app.brain.llm_choice.base import (
    ChatMessage,
    LLMError,
    LLMFatalError,
    LLMResponse,
    LLMTransientError,
    Role,
    ToolCall,
)


class TestChatMessage:
    """ChatMessage validation."""

    def test_valid_construction(self) -> None:
        msg = ChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_all_roles_accepted(self) -> None:
        for role in ("system", "user", "assistant"):
            msg = ChatMessage(role=role, content="x")
            assert msg.role == role

    def test_invalid_role(self) -> None:
        with pytest.raises(Exception, match="Input should be"):
            ChatMessage(role="invalid", content="x")  # type: ignore[arg-type]


class TestLLMResponse:
    """LLMResponse defaults and construction."""

    def test_default_values(self) -> None:
        resp = LLMResponse()
        assert resp.text is None
        assert resp.tool_calls == []

    def test_with_text(self) -> None:
        resp = LLMResponse(text="Hello")
        assert resp.text == "Hello"

    def test_with_tool_calls(self) -> None:
        tc = ToolCall(name="search", arguments={"q": "test"})
        resp = LLMResponse(text="x", tool_calls=[tc])
        assert len(resp.tool_calls) == 1


class TestToolCall:
    def test_construction(self) -> None:
        tc = ToolCall(name="calc", arguments={"expr": "2+2"})
        assert tc.name == "calc"
        assert tc.arguments == {"expr": "2+2"}


class TestErrorHierarchy:
    """LLMError exception hierarchy."""

    def test_transient_is_llm_error(self) -> None:
        assert issubclass(LLMTransientError, LLMError)

    def test_fatal_is_llm_error(self) -> None:
        assert issubclass(LLMFatalError, LLMError)

    def test_llm_error_is_exception(self) -> None:
        assert issubclass(LLMError, Exception)

    def test_catch_llm_error_catches_both(self) -> None:
        with pytest.raises(LLMError):
            raise LLMTransientError("retry")

    def test_separate_catch(self) -> None:
        with pytest.raises(LLMFatalError):
            raise LLMFatalError("skip")


class TestRoleType:
    """Role literal type."""

    def test_all_valid_roles(self) -> None:
        valid: list[Role] = ["dialogue", "cs", "bp", "vs"]
        assert len(valid) == 4
