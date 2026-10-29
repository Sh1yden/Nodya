"""Application runtime status from the Redis heartbeat."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.brain.memory.short.redis import RedisClient

HEALTHY_MAX_AGE_SECONDS = 75.0
STALE_MAX_AGE_SECONDS = 90.0


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """Freshness of the application heartbeat."""

    status: str
    payload: dict[str, Any] | None = None
    age_seconds: float | None = None


def _heartbeat_age(payload: dict[str, Any], now: datetime) -> float | None:
    """Compute the heartbeat age in seconds.

    Args:
        payload: Heartbeat body with an ISO ``heartbeat_at`` field.
        now: Reference time.

    Returns:
        Age in seconds, or None when the stamp is unparsable.
    """
    raw = payload.get("heartbeat_at")
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return max(0.0, (now - stamp).total_seconds())


class RuntimeCollector:
    """Translate the heartbeat key into a status label."""

    def __init__(self, redis: RedisClient) -> None:
        """Configure the collector.

        Args:
            redis: Shared Redis client holding the heartbeat.
        """
        self._redis = redis

    async def get_status(self) -> RuntimeStatus:
        """Read and classify the application heartbeat.

        Returns:
            ``healthy`` up to 75 s, ``stale`` up to 90 s, else
            ``missing`` when the key is absent or too old.
        """
        payload = await self._redis.get_runtime_heartbeat()
        if payload is None:
            return RuntimeStatus(status="missing")
        age = _heartbeat_age(payload, datetime.now(UTC))
        if age is None:
            return RuntimeStatus(status="healthy", payload=payload)
        if age <= HEALTHY_MAX_AGE_SECONDS:
            return RuntimeStatus(
                status="healthy", payload=payload, age_seconds=age
            )
        if age <= STALE_MAX_AGE_SECONDS:
            return RuntimeStatus(
                status="stale", payload=payload, age_seconds=age
            )
        return RuntimeStatus(
            status="missing", payload=payload, age_seconds=age
        )
