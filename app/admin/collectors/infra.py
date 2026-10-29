"""Dependency pings and infrastructure statistics for the panel."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brain.memory.short.redis import RedisClient

PingCallable = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """Reachability of one external dependency."""

    name: str
    ok: bool


@dataclass(frozen=True, slots=True)
class PostgresStats:
    """Database size and connection count."""

    database_size_bytes: int | None = None
    active_connections: int | None = None


@dataclass(frozen=True, slots=True)
class RedisStats:
    """Memory usage and client count from Redis INFO."""

    used_memory_bytes: int | None = None
    connected_clients: int | None = None
    uptime_seconds: int | None = None
    version: str | None = None


@dataclass(frozen=True, slots=True)
class QdrantCollectionInfo:
    """A Qdrant collection with its point count."""

    name: str
    points_count: int | None = None
    status: str | None = None


def _as_int(value: Any) -> int | None:
    """Convert an INFO value to int, tolerating strings and junk.

    Args:
        value: Raw value from a stats source.

    Returns:
        Parsed integer or None.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class InfraCollector:
    """Collect dependency health and storage statistics."""

    def __init__(
        self,
        *,
        pings: dict[str, PingCallable],
        qdrant_url: str,
        client: httpx.AsyncClient,
        redis: RedisClient,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Configure the collector.

        Args:
            pings: Named reachability probes, e.g. ``{"postgres": ...}``.
            qdrant_url: Base URL of the Qdrant HTTP API.
            client: Shared httpx client.
            redis: Shared Redis client.
            session_factory: Session factory for PostgreSQL statistics.
        """
        self._pings = pings
        self._qdrant_url = qdrant_url.rstrip("/")
        self._client = client
        self._redis = redis
        self._session_factory = session_factory

    async def check_dependencies(self) -> list[DependencyStatus]:
        """Run all configured pings concurrently.

        A raising ping counts as down and never fails the batch.

        Returns:
            One status per configured dependency, in config order.
        """
        names = list(self._pings)
        results = await asyncio.gather(*(self._ping(name) for name in names))
        return list(results)

    async def _ping(self, name: str) -> DependencyStatus:
        """Run a single probe without raising.

        Args:
            name: Registered probe name.

        Returns:
            The probe outcome.
        """
        try:
            ok = bool(await self._pings[name]())
        except Exception:
            ok = False
        return DependencyStatus(name=name, ok=ok)

    async def get_postgres_stats(self) -> PostgresStats:
        """Read database size and active connections.

        Returns:
            Aggregated PostgreSQL statistics.
        """
        stmt = text(
            "SELECT pg_database_size(current_database()) AS size_bytes, "
            "(SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database()) AS active_connections"
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).one()
        return PostgresStats(
            database_size_bytes=_as_int(row.size_bytes),
            active_connections=_as_int(row.active_connections),
        )

    async def get_redis_stats(self) -> RedisStats:
        """Read memory and client counters from Redis INFO.

        Returns:
            Aggregated Redis statistics.
        """
        info = await self._redis.info()
        return RedisStats(
            used_memory_bytes=_as_int(info.get("used_memory")),
            connected_clients=_as_int(info.get("connected_clients")),
            uptime_seconds=_as_int(info.get("uptime_in_seconds")),
            version=str(info["redis_version"])
            if info.get("redis_version")
            else None,
        )

    async def get_qdrant_collections(self) -> list[QdrantCollectionInfo]:
        """List Qdrant collections with point counts.

        Returns:
            Collections sorted by name.
        """
        response = await self._client.get(f"{self._qdrant_url}/collections")
        if response.status_code != 200:
            raise RuntimeError(
                f"Qdrant /collections returned {response.status_code}"
            )
        payload = response.json() or {}
        names = [
            str(item.get("name"))
            for item in (payload.get("result") or {}).get("collections") or []
            if item.get("name")
        ]
        details = await asyncio.gather(
            *(self._collection_detail(name) for name in names)
        )
        return sorted(details, key=lambda item: item.name)

    async def _collection_detail(self, name: str) -> QdrantCollectionInfo:
        """Fetch point count and status of one collection.

        Args:
            name: Collection name.

        Returns:
            Info with details when the request succeeds.
        """
        try:
            response = await self._client.get(
                f"{self._qdrant_url}/collections/{quote(name, safe='')}"
            )
            if response.status_code != 200:
                return QdrantCollectionInfo(name=name)
            result = (response.json() or {}).get("result") or {}
            status = result.get("status")
            return QdrantCollectionInfo(
                name=name,
                points_count=_as_int(result.get("points_count")),
                status=str(status) if status else None,
            )
        except Exception:
            return QdrantCollectionInfo(name=name)
