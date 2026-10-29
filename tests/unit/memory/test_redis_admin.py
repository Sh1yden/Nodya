"""Unit tests for admin/runtime helpers of ``RedisClient``."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.brain.memory.short import RedisClient


@pytest.fixture()
def client() -> RedisClient:
    """RedisClient with the low-level connection replaced by a mock."""
    redis = RedisClient("redis://localhost:6379/0")
    redis._redis = AsyncMock()
    return redis


class TestAdminSessions:
    async def test_set_admin_session_stores_json_with_ttl(
        self, client: RedisClient
    ) -> None:
        await client.set_admin_session("hash", {"user": "owner"}, ttl=60)

        args, kwargs = client._redis.set.await_args
        assert args[0] == "nodya:admin:session:hash"
        assert json.loads(args[1]) == {"user": "owner"}
        assert kwargs["ex"] == 60

    async def test_get_admin_session_parses_payload(
        self, client: RedisClient
    ) -> None:
        client._redis.get = AsyncMock(
            return_value=json.dumps({"user": "owner"})
        )

        session = await client.get_admin_session("hash")

        assert session == {"user": "owner"}
        client._redis.get.assert_awaited_once_with("nodya:admin:session:hash")

    async def test_get_admin_session_missing(
        self, client: RedisClient
    ) -> None:
        client._redis.get = AsyncMock(return_value=None)
        assert await client.get_admin_session("hash") is None

    async def test_get_admin_session_drops_damaged_json(
        self, client: RedisClient
    ) -> None:
        client._redis.get = AsyncMock(return_value="{not json")
        assert await client.get_admin_session("hash") is None
        client._redis.delete.assert_awaited_once_with(
            "nodya:admin:session:hash"
        )

    async def test_get_admin_session_rejects_non_dict(
        self, client: RedisClient
    ) -> None:
        client._redis.get = AsyncMock(return_value="[1, 2]")
        assert await client.get_admin_session("hash") is None

    async def test_delete_admin_session(self, client: RedisClient) -> None:
        await client.delete_admin_session("hash")
        client._redis.delete.assert_awaited_once_with(
            "nodya:admin:session:hash"
        )


class TestLoginFailures:
    async def test_first_failure_sets_expiry(
        self, client: RedisClient
    ) -> None:
        client._redis.incr = AsyncMock(return_value=1)

        count = await client.register_login_failure("ip", ttl=900)

        assert count == 1
        client._redis.expire.assert_awaited_once_with(
            "nodya:admin:login_fail:ip", 900
        )

    async def test_later_failures_keep_window(
        self, client: RedisClient
    ) -> None:
        client._redis.incr = AsyncMock(return_value=3)

        count = await client.register_login_failure("ip", ttl=900)

        assert count == 3
        client._redis.expire.assert_not_awaited()

    async def test_reset_login_failures(self, client: RedisClient) -> None:
        await client.reset_login_failures("ip")
        client._redis.delete.assert_awaited_once_with(
            "nodya:admin:login_fail:ip"
        )


class TestRuntimeHeartbeat:
    async def test_set_runtime_heartbeat(self, client: RedisClient) -> None:
        await client.set_runtime_heartbeat({"pid": 1}, ttl=90)

        args, kwargs = client._redis.set.await_args
        assert args[0] == "nodya:runtime:heartbeat"
        assert json.loads(args[1]) == {"pid": 1}
        assert kwargs["ex"] == 90

    async def test_get_runtime_heartbeat(self, client: RedisClient) -> None:
        client._redis.get = AsyncMock(return_value=json.dumps({"pid": 1}))
        assert await client.get_runtime_heartbeat() == {"pid": 1}

    async def test_get_runtime_heartbeat_damaged(
        self, client: RedisClient
    ) -> None:
        client._redis.get = AsyncMock(return_value="broken")
        assert await client.get_runtime_heartbeat() is None

    async def test_delete_runtime_heartbeat(self, client: RedisClient) -> None:
        await client.delete_runtime_heartbeat()
        client._redis.delete.assert_awaited_once_with(
            "nodya:runtime:heartbeat"
        )
