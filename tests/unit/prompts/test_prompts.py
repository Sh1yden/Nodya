"""Unit tests for ``app.brain.memory.init.prompts``."""

from __future__ import annotations

from pathlib import Path

from app.brain.memory.init.prompts import (
    _read_or_default,
    load_system_prompt,
)


class TestReadOrDefault:
    """Test _read_or_default fallback logic."""

    def test_existing_file_with_content(self, tmp_path: Path) -> None:
        p = tmp_path / "prompt.md"
        p.write_text("Custom prompt", encoding="utf-8")

        result = _read_or_default(p, "default", "test.md")
        assert result == "Custom prompt"

    def test_empty_file_falls_back(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.md"
        p.write_text("", encoding="utf-8")

        result = _read_or_default(p, "fallback", "empty.md")
        assert result == "fallback"

    def test_whitespace_only_file_falls_back(self, tmp_path: Path) -> None:
        p = tmp_path / "whitespace.md"
        p.write_text("   \n  ", encoding="utf-8")

        result = _read_or_default(p, "fallback", "ws.md")
        assert result == "fallback"

    def test_missing_file_falls_back(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.md"

        result = _read_or_default(p, "fallback", "no.md")
        assert result == "fallback"


class TestLoadSystemPrompt:
    """Test load_system_prompt assembly."""

    def test_returns_string(self) -> None:
        result = load_system_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_default_content(self) -> None:
        # When prompt files are empty (as in the repo), defaults are used
        result = load_system_prompt()
        # Should contain either file content or defaults
        assert "\n\n" in result  # ME + double newline + RULES
