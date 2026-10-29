"""TTL cache with per-key locking and stale-on-error semantics."""

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.admin.collectors.base import Result

Loader = Callable[[], Awaitable[Result]]


class TTLCache:
    """Cache one Result per key for a short TTL.

    A failing refresh does not blank a page that had data moments
    ago: the previous Result is served with ``stale=True`` until the
    stale window ends. Failures without cached data are remembered
    for a short negative TTL so a dead dependency is not hammered.
    """

    def __init__(
        self,
        ttl: float,
        stale_ttl: float = 60.0,
        negative_ttl: float = 5.0,
        timeout: float = 8.0,
    ) -> None:
        """Configure cache windows.

        Args:
            ttl: Freshness window for successful results.
            stale_ttl: How long the last good result may be served.
            negative_ttl: Freshness window for error-only results.
            timeout: Hard limit on one loader call, seconds.
        """
        self._ttl = ttl
        self._stale_ttl = stale_ttl
        self._negative_ttl = negative_ttl
        self._timeout = timeout
        self._entries: dict[str, tuple[float, Result]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _fresh(self, key: str, now: float) -> Result | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, result = entry
        ttl = self._ttl if result.ok else self._negative_ttl
        return result if now - stored_at <= ttl else None

    async def get(self, key: str, loader: Loader) -> Result:
        """Return a fresh or refreshed result for the key.

        Args:
            key: Logical cache key.
            loader: Coroutine producing a Result on miss.

        Returns:
            The loader result, a cached one, or a stale copy.
        """
        now = time.monotonic()
        cached = self._fresh(key, now)
        if cached is not None:
            return cached

        async with self._lock(key):
            now = time.monotonic()
            cached = self._fresh(key, now)
            if cached is not None:
                return cached
            try:
                async with asyncio.timeout(self._timeout):
                    result = await loader()
            except Exception as exc:
                result = Result(
                    data=None, error=str(exc) or type(exc).__name__
                )
            previous = self._entries.get(key)
            if (
                not result.ok
                and previous is not None
                and previous[1].ok
                and now - previous[0] <= self._stale_ttl
            ):
                return Result(
                    data=previous[1].data,
                    error=result.error,
                    fetched_at=previous[1].fetched_at,
                    stale=True,
                )
            self._entries[key] = (now, result)
            return result
