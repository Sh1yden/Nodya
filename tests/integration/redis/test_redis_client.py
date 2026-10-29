"""Integration tests for ``app.brain.memory.short.redis``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from app.brain.memory.short.redis import (
    RedisClient,
)


def _make_pipeline_mock() -> MagicMock:
    """Create a mock that behaves as an async context manager for pipeline."""
    pipe = MagicMock()
    pipe.hset = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[])

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=pipe)
    ctx.__aexit__ = AsyncMock(return_value=False)

    return ctx


class TestRedisClient:
    """Test RedisClient methods with mocked redis.asyncio.Redis."""

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_ping_success(self, mock_from_url: Mock) -> None:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")
        result = await client.ping()
        assert result is True

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_ping_failure(self, mock_from_url: Mock) -> None:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("down"))
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")
        result = await client.ping()
        assert result is False

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_acquire_lock_returns_token(
        self, mock_from_url: Mock
    ) -> None:
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")
        token = await client.acquire_lock(uuid.uuid4(), ttl=30)
        assert token is not None
        assert isinstance(token, str)

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_acquire_lock_busy_returns_none(
        self, mock_from_url: Mock
    ) -> None:
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=False)
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")
        token = await client.acquire_lock(uuid.uuid4(), ttl=30)
        assert token is None

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_set_and_get_state(self, mock_from_url: Mock) -> None:
        mock_redis = AsyncMock()
        mock_redis.hset = AsyncMock()
        mock_redis.hgetall = AsyncMock(
            return_value={
                "status": "thinking",
                "last_active_at": datetime.now(UTC).isoformat(),
            }
        )
        mock_redis.register_script = Mock(return_value=AsyncMock())
        # pipeline() is sync in redis.asyncio, returns a context manager
        mock_redis.pipeline = Mock(return_value=_make_pipeline_mock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")
        uid = uuid.uuid4()

        await client.set_state(uid, "thinking")

        state = await client.get_state(uid)
        assert state is not None
        assert state.status == "thinking"

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_issue_link_code(self, mock_from_url: Mock) -> None:
        mock_redis = AsyncMock()
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[True, True])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=pipe)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = Mock(return_value=ctx)
        mock_redis.delete = AsyncMock()
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")
        uid = uuid.uuid4()

        code = await client.issue_link_code(uid)
        assert isinstance(code, str)
        assert len(code) == 8

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_claim_feed_item_first_wins(
        self, mock_from_url: Mock
    ) -> None:
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=[True, False])
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")

        assert await client.claim_feed_item("digest", 60) is True
        assert await client.claim_feed_item("digest", 60) is False
        assert mock_redis.set.await_args.kwargs == {"nx": True, "ex": 60}

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_push_and_pop_debounce(self, mock_from_url: Mock) -> None:
        mock_redis = AsyncMock()

        # push_debounce uses pipeline
        push_pipe = MagicMock()
        push_pipe.execute = AsyncMock(return_value=[1, True])
        push_ctx = MagicMock()
        push_ctx.__aenter__ = AsyncMock(return_value=push_pipe)
        push_ctx.__aexit__ = AsyncMock(return_value=False)

        # pop_debounce_batch uses pipeline
        pop_pipe = MagicMock()
        pop_pipe.execute = AsyncMock(return_value=(["msg1"], 1))
        pop_ctx = MagicMock()
        pop_ctx.__aenter__ = AsyncMock(return_value=pop_pipe)
        pop_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_redis.pipeline = Mock(side_effect=[push_ctx, pop_ctx])
        mock_redis.lrange = AsyncMock(return_value=["msg1"])
        mock_redis.delete = AsyncMock()
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")
        uid = uuid.uuid4()

        size = await client.push_debounce(uid, "hello")
        assert size == 1

        batch = await client.pop_debounce_batch(uid)
        assert batch == ["msg1"]

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_consume_daily_quota_allows_first_calls(
        self, mock_from_url: Mock
    ) -> None:
        mock_redis = AsyncMock()
        counts = [[1, True], [2, True], [3, True]]
        pipe = MagicMock()
        pipe.incr = MagicMock()
        pipe.expire = MagicMock()
        pipe.execute = AsyncMock(side_effect=counts)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=pipe)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_redis.pipeline = Mock(return_value=ctx)
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")

        assert await client.consume_daily_quota("feed_notify:x", 2) is True
        assert await client.consume_daily_quota("feed_notify:x", 2) is True
        assert await client.consume_daily_quota("feed_notify:x", 2) is False

        day = datetime.now(UTC).date().isoformat()
        key = pipe.incr.call_args.args[0]
        assert key == f"nodya:quota:feed_notify:x:{day}"
        assert pipe.expire.call_args.args[1] == 86400

    @patch("app.brain.memory.short.redis.Redis.from_url")
    async def test_consume_daily_quota_zero_limit_forbids(
        self, mock_from_url: Mock
    ) -> None:
        mock_redis = AsyncMock()
        mock_redis.pipeline = Mock()
        mock_redis.register_script = Mock(return_value=AsyncMock())
        mock_from_url.return_value = mock_redis

        client = RedisClient("redis://localhost:6379")

        assert await client.consume_daily_quota("bucket", 0) is False
        mock_redis.pipeline.assert_not_called()
