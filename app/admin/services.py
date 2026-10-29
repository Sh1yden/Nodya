"""AdminServices: shared clients, collectors and their caches."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.admin.cache import TTLCache
from app.admin.collectors import (
    BrokerCollector,
    ContainerInfo,
    DockerCollector,
    ErrorRecord,
    ErrorsCollector,
    InfraCollector,
    LogFileInfo,
    LogsCollector,
    Result,
    RuntimeCollector,
    RuntimeStatus,
)
from app.api.health import ping_postgres, ping_qdrant
from app.brain.memory.short.redis import RedisClient
from app.core.config import SettingsSchema

_HTTP_TIMEOUT_SECONDS = 5.0

_CACHE_TTLS: dict[str, float] = {
    "deps": 10.0,
    "containers": 5.0,
    "container_logs": 2.0,
    "queues": 5.0,
    "errors": 10.0,
    "error": 10.0,
    "log_files": 10.0,
    "logs": 2.0,
    "postgres": 10.0,
    "redis": 10.0,
    "qdrant": 10.0,
    "runtime": 5.0,
}


def _data_loader(
    factory: Callable[[], Awaitable[Any]],
) -> Callable[[], Awaitable[Result]]:
    """Wrap a plain collector coroutine into a Result loader.

    Args:
        factory: Callable returning the collector coroutine.

    Returns:
        Loader suitable for ``TTLCache.get``.
    """

    async def load() -> Result:
        return Result(data=await factory())

    return load


class AdminServices:
    """Single facade used by routes and dependencies."""

    def __init__(
        self,
        settings: SettingsSchema,
        redis: RedisClient,
        session_factory: async_sessionmaker[AsyncSession],
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Wire clients, collectors and caches.

        Args:
            settings: Application settings.
            redis: Shared Redis client (sessions, heartbeat, INFO).
            session_factory: Session factory for read-only queries.
            http_client: Optional preconfigured client; tests inject a
                MockTransport-backed one.
        """
        self.settings = settings
        self.redis = redis
        self.session_factory = session_factory
        self._owns_client = http_client is None
        self.http = http_client or httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_SECONDS
        )
        self.docker = DockerCollector(
            client=self.http,
            base_url=settings.DOCKER_PROXY_URL,
            project=settings.ADMIN_COMPOSE_PROJECT,
        )
        self.broker = BrokerCollector(settings.rabbitmq_url)
        self.infra = InfraCollector(
            pings={
                "postgres": ping_postgres,
                "rabbitmq": self.broker.ping,
                "redis": redis.ping,
                "qdrant": ping_qdrant,
                "docker": self.docker.ping,
            },
            qdrant_url=(
                f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}"
            ),
            client=self.http,
            redis=redis,
            session_factory=session_factory,
        )
        self.logs = LogsCollector(Path(settings.ADMIN_LOGS_DIR))
        self.errors = ErrorsCollector(session_factory)
        self.runtime = RuntimeCollector(redis)
        self._caches = {
            name: TTLCache(ttl=ttl) for name, ttl in _CACHE_TTLS.items()
        }

    async def _cached(
        self, group: str, key: str, loader: Callable[[], Awaitable[Result]]
    ) -> Result:
        """Fetch through the cache of one data group.

        Args:
            group: Cache group name from ``_CACHE_TTLS``.
            key: Cache key inside the group.
            loader: Coroutine factory producing a fresh Result.

        Returns:
            Cached or freshly loaded Result.
        """
        return await self._caches[group].get(key, loader)

    async def get_deps(self) -> Result:
        """Reachability of every external dependency.

        Returns:
            Result carrying a list of DependencyStatus.
        """
        return await self._cached(
            "deps", "all", _data_loader(self.infra.check_dependencies)
        )

    async def get_containers(self) -> Result:
        """Containers of the compose project.

        Returns:
            Result carrying a list of ContainerInfo.
        """
        return await self._cached(
            "containers", "all", _data_loader(self.docker.get_containers)
        )

    async def find_container(self, service: str) -> ContainerInfo | None:
        """Find a container by compose service name.

        Args:
            service: Compose service label value.

        Returns:
            The container, or None when absent or the list failed.
        """
        result = await self.get_containers()
        if not result.ok:
            return None
        return next(
            (item for item in result.data if item.service == service),
            None,
        )

    async def get_container_logs(
        self, container: ContainerInfo, tail: int = 200
    ) -> Result:
        """Tail the logs of one container.

        Args:
            container: Container as listed by the Docker collector.
            tail: Lines to request (clamped by the collector).

        Returns:
            Result carrying a list of ContainerLogLine.
        """
        key = f"{container.id}:{tail}"
        return await self._cached(
            "container_logs",
            key,
            _data_loader(lambda: self.docker.get_logs(container.id, tail)),
        )

    async def get_queues(self) -> Result:
        """Queue depths and consumer counts.

        Returns:
            Result carrying a list of QueueInfo.
        """
        return await self._cached(
            "queues", "all", _data_loader(self.broker.get_queues)
        )

    async def get_errors(
        self, limit: int = 50, level: str | None = None
    ) -> Result:
        """Most recent error groups.

        Args:
            limit: Maximum rows.
            level: Exact level filter (uppercase).

        Returns:
            Result carrying a list of ErrorRecord.
        """
        key = f"{level or 'all'}:{limit}"
        return await self._cached(
            "errors",
            key,
            _data_loader(lambda: self.errors.list_errors(limit, level)),
        )

    async def get_error(self, fingerprint: str) -> Result:
        """One error group by fingerprint.

        Args:
            fingerprint: 64-character hex digest.

        Returns:
            Result carrying an ErrorRecord or None.
        """
        key = fingerprint
        return await self._cached(
            "error",
            key,
            _data_loader(lambda: self.errors.get_error(fingerprint)),
        )

    async def get_log_files(self) -> Result:
        """Log files available on the shared volume.

        Returns:
            Result carrying a list of LogFileInfo.
        """
        return await self._cached(
            "log_files", "all", _data_loader(self.logs.list_files)
        )

    async def get_logs(
        self,
        filename: str,
        level: str | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> Result:
        """Filtered tail of one log file.

        Args:
            filename: Whitelisted log file name.
            level: Exact level filter (uppercase).
            query: Case-insensitive substring filter.
            limit: Maximum entries.

        Returns:
            Result carrying a LogSlice.
        """
        key = f"{filename}:{level or '-'}:{query or '-'}:{limit}"
        return await self._cached(
            "logs",
            key,
            _data_loader(
                lambda: self.logs.tail(filename, level, query, limit)
            ),
        )

    async def get_postgres_stats(self) -> Result:
        """Database size and active connection count.

        Returns:
            Result carrying PostgresStats.
        """
        return await self._cached(
            "postgres", "all", _data_loader(self.infra.get_postgres_stats)
        )

    async def get_redis_stats(self) -> Result:
        """Redis memory and client counters.

        Returns:
            Result carrying RedisStats.
        """
        return await self._cached(
            "redis", "all", _data_loader(self.infra.get_redis_stats)
        )

    async def get_qdrant_stats(self) -> Result:
        """Qdrant collections and point counts.

        Returns:
            Result carrying a list of QdrantCollectionInfo.
        """
        return await self._cached(
            "qdrant", "all", _data_loader(self.infra.get_qdrant_collections)
        )

    async def get_runtime(self) -> Result:
        """Application heartbeat status.

        Returns:
            Result carrying RuntimeStatus.
        """
        return await self._cached(
            "runtime", "all", _data_loader(self.runtime.get_status)
        )

    async def close(self) -> None:
        """Release connections owned by the services."""
        await self.broker.close()
        if self._owns_client:
            await self.http.aclose()


__all__ = [
    "AdminServices",
    "ErrorRecord",
    "LogFileInfo",
    "Result",
    "RuntimeStatus",
]
