"""Unit tests for the heartbeat runtime collector."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.admin.collectors.runtime import RuntimeCollector, RuntimeStatus


def _redis(payload: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        get_runtime_heartbeat=AsyncMock(return_value=payload)
    )


def _payload(age_seconds: float) -> dict:
    stamp = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return {
        "pid": 42,
        "version": "dev",
        "heartbeat_at": stamp.isoformat(),
        "jobs": [],
    }


async def test_missing_heartbeat() -> None:
    status = await RuntimeCollector(_redis(None)).get_status()
    assert status == RuntimeStatus(status="missing")


async def test_healthy_heartbeat() -> None:
    payload = _payload(5)
    status = await RuntimeCollector(_redis(payload)).get_status()
    assert status.status == "healthy"
    assert status.payload == payload
    assert status.age_seconds is not None
    assert status.age_seconds < 10


async def test_stale_heartbeat() -> None:
    status = await RuntimeCollector(_redis(_payload(80))).get_status()
    assert status.status == "stale"
    assert status.age_seconds is not None


async def test_too_old_heartbeat_is_missing() -> None:
    status = await RuntimeCollector(_redis(_payload(120))).get_status()
    assert status.status == "missing"


async def test_unparsable_stamp_still_healthy() -> None:
    payload = {"heartbeat_at": "garbage", "pid": 1}
    status = await RuntimeCollector(_redis(payload)).get_status()
    assert status.status == "healthy"
    assert status.age_seconds is None


async def test_naive_stamp_is_treated_as_utc() -> None:
    stamp = (datetime.now(UTC) - timedelta(seconds=5)).replace(tzinfo=None)
    status = await RuntimeCollector(
        _redis({"heartbeat_at": stamp.isoformat()})
    ).get_status()
    assert status.status == "healthy"
