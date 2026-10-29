"""Unit tests for ``app.core.runtime_status``."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from app.core.runtime_status import heartbeat_loop


class _FakeRedis:
    """Stand-in exposing only the heartbeat method."""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[tuple[dict, int]] = []
        self._fail_times = fail_times

    async def set_runtime_heartbeat(self, payload: dict, ttl: int) -> None:
        self.calls.append((payload, ttl))
        if len(self.calls) <= self._fail_times:
            raise RuntimeError("redis down")


async def test_heartbeat_publishes_payload() -> None:
    redis = _FakeRedis()

    task = asyncio.create_task(
        heartbeat_loop(redis, lambda: {"pid": 1}, interval=1, ttl=90)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert redis.calls[0] == ({"pid": 1}, 90)


async def test_heartbeat_survives_redis_errors() -> None:
    redis = _FakeRedis(fail_times=1)

    task = asyncio.create_task(
        heartbeat_loop(redis, lambda: {"pid": 2}, interval=0.01, ttl=90)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert len(redis.calls) >= 2
