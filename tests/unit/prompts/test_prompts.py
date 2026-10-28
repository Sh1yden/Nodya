"""Unit tests for ``app.brain.memory.init.prompts``."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.brain.memory.init import prompts
from app.brain.memory.init.prompts import (
    _read_or_default,
    build_interlocutor_block,
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


class TestCreatorPrompt:
    """CREATOR.md must be visible to the owner only."""

    @pytest.fixture()
    def prompts_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "ME.md").write_text("me", encoding="utf-8")
        (tmp_path / "RULES.md").write_text("rules", encoding="utf-8")
        (tmp_path / "CREATOR.md").write_text(
            "creator secret", encoding="utf-8"
        )
        return tmp_path

    def test_owner_gets_creator_file(
        self, monkeypatch: pytest.MonkeyPatch, prompts_dir: Path
    ) -> None:
        monkeypatch.setattr(prompts, "PROMPTS_DIR", prompts_dir)
        result = load_system_prompt(is_owner=True)
        assert "creator secret" in result

    def test_regular_user_does_not(
        self, monkeypatch: pytest.MonkeyPatch, prompts_dir: Path
    ) -> None:
        monkeypatch.setattr(prompts, "PROMPTS_DIR", prompts_dir)
        result = load_system_prompt(is_owner=False)
        assert "creator secret" not in result


class TestBuildInterlocutorBlock:
    """The block must name the sender and forbid identity mixing."""

    def test_regular_user_fields(self) -> None:
        result = build_interlocutor_block(
            name="Иван",
            username="ivan",
            external_id="1188741734",
            channel="telegram",
            is_owner=False,
        )
        assert "Иван" in result
        assert "@ivan" in result
        assert "1188741734" in result
        assert "НЕ твой создатель" in result
        assert "только к этому человеку" in result

    def test_owner_role(self) -> None:
        result = build_interlocutor_block(
            name="Shayden",
            username=None,
            external_id="42",
            channel="telegram",
            is_owner=True,
        )
        assert "владелец и создатель" in result
        assert "НЕ твой создатель" not in result

    def test_missing_name_uses_fallback(self) -> None:
        result = build_interlocutor_block(
            name=None,
            username=None,
            external_id=None,
            channel="telegram",
            is_owner=False,
        )
        assert "без имени" in result
