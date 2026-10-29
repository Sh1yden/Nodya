"""Unit tests for the error group collector."""

from datetime import UTC, datetime

from app.admin.collectors.errors import ErrorsCollector, _to_record
from app.brain.models import ErrorEvents


def _row(message: str, traceback: str | None = None) -> ErrorEvents:
    now = datetime.now(UTC)
    return ErrorEvents(
        fingerprint="a" * 64,
        level="ERROR",
        logger="nodya.worker",
        module="worker",
        function="_flush",
        exception_type="RuntimeError",
        message=message,
        traceback=traceback,
        count=3,
        first_seen=now,
        last_seen=now,
    )


def test_to_record_redacts_secrets() -> None:
    record = _to_record(
        _row(
            "api_key=super-secret failed",
            "Authorization: Bearer abcdef123456",
        )
    )
    assert "super-secret" not in record.message
    assert "api_key=<redacted>" in record.message
    assert "abcdef123456" not in (record.traceback or "")
    assert record.count == 3
    assert record.exception_type == "RuntimeError"


def test_to_record_without_traceback() -> None:
    record = _to_record(_row("plain"))
    assert record.traceback is None


class _BoomFactory:
    def __call__(self) -> object:
        raise AssertionError("database must not be touched")


async def test_get_error_rejects_bad_fingerprint() -> None:
    collector = ErrorsCollector(_BoomFactory())
    assert await collector.get_error("not-hex") is None
    assert await collector.get_error("A" * 64) is None
    assert await collector.get_error("") is None
