"""Integration tests for ``app.brain.memory.consolidation``."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.brain.memory.consolidation import (
    Analysis,
    ConsolidationJob,
    ExtractedFact,
    _parse_json,
)
from app.brain.memory.short.redis import ContextMessage


def _make_session_factory(
    session: AsyncMock,
) -> AsyncGenerator:
    """Return an async context manager factory."""

    @asynccontextmanager
    async def _factory() -> AsyncGenerator:
        yield session

    return _factory


class TestParseJson:
    def test_valid_json(self) -> None:
        raw = '{"facts": [], "summary": "ok"}'
        result = _parse_json(raw)
        assert result["summary"] == "ok"

    def test_json_in_markdown_fences(self) -> None:
        raw = '```json\n{"facts": [], "summary": "test"}\n```'
        result = _parse_json(raw)
        assert result["summary"] == "test"

    def test_json_with_surrounding_text(self) -> None:
        raw = 'Here is the result: {"facts": [], "summary": "x"} done.'
        result = _parse_json(raw)
        assert result["summary"] == "x"

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON"):
            _parse_json("no json here at all")


class TestExtractedFact:
    def test_valid_construction(self) -> None:
        fact = ExtractedFact(
            category="prefs",
            key="theme",
            value="dark",
            confidence=0.8,
        )
        assert fact.category == "prefs"
        assert fact.confidence == 0.8

    def test_confidence_out_of_range(self) -> None:
        with pytest.raises(Exception, match="less than or equal"):
            ExtractedFact(
                category="a",
                key="b",
                value="c",
                confidence=1.5,
            )


class TestAnalysis:
    def test_valid_construction(self) -> None:
        analysis = Analysis(facts=[], summary="All good")
        assert analysis.summary == "All good"


class TestConsolidationJobRunUser:
    async def test_skip_when_context_too_short(
        self, mock_redis: AsyncMock
    ) -> None:
        job = ConsolidationJob.__new__(ConsolidationJob)
        object.__setattr__(job, "_redis", mock_redis)
        object.__setattr__(job, "_router", AsyncMock())
        object.__setattr__(job, "_vectors", AsyncMock())
        object.__setattr__(job, "_session_factory", AsyncMock())
        logger = MagicMock()
        object.__setattr__(job, "_logger", logger)

        mock_redis.acquire_lock = AsyncMock(return_value="token")
        mock_redis.release_lock = AsyncMock(return_value=True)
        mock_redis.get_context = AsyncMock(return_value=[])
        mock_redis.set_state = AsyncMock()

        result = await job.run_user(uuid.uuid4())
        assert result is False

    async def test_skip_when_lock_busy(self, mock_redis: AsyncMock) -> None:
        job = ConsolidationJob.__new__(ConsolidationJob)
        object.__setattr__(job, "_redis", mock_redis)
        object.__setattr__(job, "_router", AsyncMock())
        object.__setattr__(job, "_vectors", AsyncMock())
        object.__setattr__(job, "_session_factory", AsyncMock())
        logger = MagicMock()
        object.__setattr__(job, "_logger", logger)

        mock_redis.acquire_lock = AsyncMock(return_value=None)

        result = await job.run_user(uuid.uuid4())
        assert result is False

    async def test_successful_consolidation(
        self, mock_redis: AsyncMock
    ) -> None:
        job = ConsolidationJob.__new__(ConsolidationJob)
        object.__setattr__(job, "_redis", mock_redis)
        router = AsyncMock()
        object.__setattr__(job, "_router", router)
        object.__setattr__(job, "_vectors", AsyncMock())
        object.__setattr__(job, "_session_factory", AsyncMock())
        logger = MagicMock()
        object.__setattr__(job, "_logger", logger)

        mock_redis.acquire_lock = AsyncMock(return_value="token")
        mock_redis.release_lock = AsyncMock(return_value=True)
        mock_redis.set_state = AsyncMock()

        history = [
            ContextMessage(
                role="user",
                content="I prefer dark theme",
                timestamp=datetime.now(UTC),
            ),
            ContextMessage(
                role="assistant",
                content="Got it, dark theme noted.",
                timestamp=datetime.now(UTC),
            ),
        ] * 5
        mock_redis.get_context = AsyncMock(return_value=history)
        mock_redis.replace_context = AsyncMock()

        analysis = Analysis(
            facts=[
                ExtractedFact(
                    category="prefs",
                    key="theme",
                    value="dark",
                    confidence=0.9,
                )
            ],
            summary="User prefers dark theme.",
        )
        router.generate_with_fallback = AsyncMock(
            return_value=MagicMock(text=json.dumps(analysis.model_dump()))
        )
        router.embed = AsyncMock(return_value=[[0.1]])

        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        object.__setattr__(
            job,
            "_session_factory",
            _make_session_factory(session),
        )

        result = await job.run_user(uuid.uuid4(), check_idle=False)
        assert result is True
        mock_redis.replace_context.assert_awaited()
