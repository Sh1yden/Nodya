"""Unit tests for the admin TTL cache."""

import asyncio
from collections.abc import Awaitable, Callable

from app.admin.cache import TTLCache
from app.admin.collectors import Result


def _counting(
    result_factory: Callable[[], Awaitable[Result]],
) -> tuple[Callable[[], Awaitable[Result]], list[int]]:
    calls = [0]
    current = [result_factory]

    async def loader() -> Result:
        calls[0] += 1
        return await current[0]()

    return loader, calls


async def test_fresh_result_is_cached() -> None:
    cache = TTLCache(ttl=10)

    async def factory() -> Result:
        return Result(data="value")

    loader, calls = _counting(factory)
    assert (await cache.get("key", loader)).data == "value"
    assert (await cache.get("key", loader)).data == "value"
    assert calls[0] == 1


async def test_loader_exception_becomes_error_result() -> None:
    cache = TTLCache(ttl=10)

    async def loader() -> Result:
        raise RuntimeError("boom")

    result = await cache.get("key", loader)
    assert not result.ok
    assert result.error == "boom"


async def test_stale_value_served_after_failure() -> None:
    cache = TTLCache(ttl=0.01, stale_ttl=60)

    async def good() -> Result:
        return Result(data="value")

    await cache.get("key", good)
    await asyncio.sleep(0.02)

    async def bad() -> Result:
        raise RuntimeError("down")

    result = await cache.get("key", bad)
    assert result.ok
    assert result.stale
    assert result.error == "down"
    assert result.data == "value"


async def test_stale_window_expires() -> None:
    cache = TTLCache(ttl=0.01, stale_ttl=0.02)

    async def good() -> Result:
        return Result(data="value")

    await cache.get("key", good)
    await asyncio.sleep(0.04)

    async def bad() -> Result:
        raise RuntimeError("down")

    result = await cache.get("key", bad)
    assert not result.ok
    assert not result.stale


async def test_error_results_use_negative_ttl() -> None:
    cache = TTLCache(ttl=0.01, negative_ttl=60)

    async def bad() -> Result:
        raise RuntimeError("down")

    await cache.get("key", bad)
    second = await cache.get("key", bad)
    assert second.error == "down"


async def test_loader_timeout_is_an_error() -> None:
    cache = TTLCache(ttl=10, timeout=0.01)

    async def slow() -> Result:
        await asyncio.sleep(0.5)
        return Result(data="late")

    result = await cache.get("key", slow)
    assert not result.ok
    assert result.error
