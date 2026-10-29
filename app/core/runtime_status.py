"""Runtime heartbeat published to Redis for the admin panel."""

import asyncio
from collections.abc import Callable
from typing import Any

from app.brain.memory.short import RedisClient
from app.core import get_logger

logger = get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TTL_SECONDS = 90

PayloadFactory = Callable[[], dict[str, Any]]


async def heartbeat_loop(
    redis: RedisClient,
    payload_factory: PayloadFactory,
    interval: int = HEARTBEAT_INTERVAL_SECONDS,
    ttl: int = HEARTBEAT_TTL_SECONDS,
) -> None:
    """Refresh the runtime heartbeat key until cancelled.

    Redis outages are logged and retried on the next tick: a missed
    heartbeat must never crash the application.

    Args:
        redis: Short-term memory client.
        payload_factory: Builds the JSON-serializable heartbeat body.
        interval: Seconds between refreshes.
        ttl: Key expiry; must exceed the interval.
    """
    while True:
        try:
            await redis.set_runtime_heartbeat(payload_factory(), ttl=ttl)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Runtime heartbeat failed: {exc}")
        await asyncio.sleep(interval)
