"""Unit tests for ``app.core.error_sink``."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from app.brain.repositories import ErrorEventUpsert
from app.core.error_sink import (
    ErrorEventSink,
    aggregate_events,
    make_fingerprint,
    normalize_message,
    redact_secrets,
)


def _event(fingerprint: str = "f" * 64, count: int = 1) -> ErrorEventUpsert:
    return ErrorEventUpsert(
        fingerprint=fingerprint,
        level="ERROR",
        logger="nodya.test",
        module="tests",
        function="run",
        message="boom",
        seen_at=datetime.now(UTC),
        count=count,
    )


def _factory(session: AsyncMock) -> Any:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncMock]:
        yield session

    return factory


def _compiled(stmt: Any) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class TestNormalization:
    def test_volatile_parts_collapse(self) -> None:
        raw = (
            "Failed for user 550e8400-e29b-41d4-a716-446655440000 "
            "id=42 token deadbeefdeadbeefdeadbeef"
        )

        normalized = normalize_message(raw)

        assert "<uuid>" in normalized
        assert "<n>" in normalized
        assert "<hex>" in normalized
        assert "550e8400" not in normalized

    def test_whitespace_collapses(self) -> None:
        assert normalize_message("a  \n\t b") == "a b"

    def test_fingerprint_is_stable_across_numbers(self) -> None:
        first = make_fingerprint(
            "nodya.worker", "handle", "ValueError", "user 123 broke"
        )
        second = make_fingerprint(
            "nodya.worker", "handle", "ValueError", "user 456 broke"
        )
        assert first == second
        assert len(first) == 64

    def test_fingerprint_varies_by_exception_type(self) -> None:
        first = make_fingerprint("nodya.w", "h", "ValueError", "boom")
        second = make_fingerprint("nodya.w", "h", "KeyError", "boom")
        assert first != second

    def test_redact_secrets_masks_credentials(self) -> None:
        text = (
            "password=hunter2 token: abc123 Authorization: Bearer abc.def-123"
        )

        cleaned = redact_secrets(text)

        assert "hunter2" not in cleaned
        assert "abc.def-123" not in cleaned

    def test_redact_secrets_masks_bot_tokens(self) -> None:
        cleaned = redact_secrets(
            "bot 1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ123456789 failed"
        )
        assert "ABCdef" not in cleaned
        assert "<redacted>" in cleaned


class TestAggregation:
    def test_same_fingerprint_merges(self) -> None:
        first = _event(count=1)
        second = _event(count=2)

        merged = aggregate_events([first, second])

        assert len(merged) == 1
        assert merged[0].count == 3

    def test_different_fingerprints_stay_split(self) -> None:
        merged = aggregate_events([_event("a" * 64), _event("b" * 64)])
        assert len(merged) == 2

    def test_latest_traceback_wins(self) -> None:
        first = _event()
        second = replace(first, traceback="TB")

        merged = aggregate_events([first, second])

        assert merged[0].traceback == "TB"


def _record(
    name: str = "nodya.test.sink",
    level: int = logging.ERROR,
    message: str = "boom",
    exc_info: tuple | None = None,
) -> logging.LogRecord:
    return logging.LogRecord(
        name, level, __file__, 10, message, None, exc_info
    )


class TestErrorEventSink:
    async def test_captures_only_nodya_errors(self) -> None:
        session = AsyncMock()
        sink = ErrorEventSink(_factory(session), queue_size=10)
        sink.start()

        sink.emit(_record(message="captured boom"))
        sink.emit(_record(name="uvicorn.error", message="external boom"))
        sink.emit(_record(level=logging.WARNING, message="just a warning"))
        sink.emit(_record(name="nodya.core.error_sink", message="own"))

        await asyncio.sleep(0)
        await sink.stop()

        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()
        sql = _compiled(session.execute.await_args.args[0])
        assert "captured boom" in sql
        assert "external boom" not in sql
        assert "just a warning" not in sql

    async def test_exc_info_is_stored(self) -> None:
        session = AsyncMock()
        sink = ErrorEventSink(_factory(session), queue_size=10)
        sink.start()

        try:
            raise RuntimeError("kaput")
        except RuntimeError:
            sink.emit(_record(message="exploded", exc_info=sys.exc_info()))

        await asyncio.sleep(0)
        await sink.stop()

        sql = _compiled(session.execute.await_args.args[0])
        assert "RuntimeError" in sql
        assert "kaput" in sql

    async def test_queue_overflow_drops_occurrence(self) -> None:
        session = AsyncMock()
        sink = ErrorEventSink(_factory(session), queue_size=1)
        sink._loop = asyncio.get_running_loop()
        sink._queue.put_nowait(MagicMock())

        sink.emit(_record(message="dropped"))

        await asyncio.sleep(0)
        assert sink._queue.qsize() == 1
        sink._queue.get_nowait()

    async def test_flush_failure_is_swallowed(self) -> None:
        session = AsyncMock()
        session.commit = AsyncMock(side_effect=RuntimeError("db down"))
        sink = ErrorEventSink(_factory(session), queue_size=10)
        sink.start()

        sink.emit(_record(message="boom"))
        await asyncio.sleep(0)
        await sink.stop()
