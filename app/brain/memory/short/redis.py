"""Short-term memory of Nodya on top of Redis.

Single entry point for state/context/lock/debounce/scheduled work —
the rest of the codebase never touches redis-py directly, only
through RedisClient.

Path in the project: app/brain/memory/short/redis.py
"""

import secrets
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel
from redis.asyncio import Redis

from app.core import get_logger

logger = get_logger(__name__)

_STATE_PREFIX = "nodya:state"
_CONTEXT_PREFIX = "nodya:context"
_LOCK_PREFIX = "nodya:lock"
_DEBOUNCE_PREFIX = "nodya:debounce"
_AGENT_ONLINE_PREFIX = "nodya:agent_online"
_SCHEDULED_KEY = "nodya:scheduled"
_LINK_PREFIX = "nodya:link"
LINK_TTL_SECONDS = 600
_LINK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_CONTEXT_MAX_LEN = 100
_CONTEXT_TTL_SECONDS = 24 * 60 * 60
_DEBOUNCE_SAFETY_TTL_SECONDS = 60

_RELEASE_LOCK_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

_POP_DUE_SCRIPT = """
local items = redis.call("ZRANGEBYSCORE", KEYS[1],
    "-inf", ARGV[1], "LIMIT", 0, tonumber(ARGV[2]))
if #items > 0 then
    redis.call("ZREM", KEYS[1], unpack(items))
end
return items
"""


def _state_key(user_id: UUID) -> str:
    """Build the state hash key for a user."""
    return f"{_STATE_PREFIX}:{user_id}"


def _context_key(user_id: UUID) -> str:
    """Build the context list key for a user."""
    return f"{_CONTEXT_PREFIX}:{user_id}"


def _lock_key(user_id: UUID) -> str:
    """Build the lock key for a user."""
    return f"{_LOCK_PREFIX}:{user_id}"


def _debounce_key(user_id: UUID) -> str:
    """Build the debounce buffer key for a user."""
    return f"{_DEBOUNCE_PREFIX}:{user_id}"


def _agent_online_key(user_id: UUID) -> str:
    """Build the agent presence key for a user."""
    return f"{_AGENT_ONLINE_PREFIX}:{user_id}"


class DialogueState(BaseModel):
    """Nodya's attitude towards a dialogue with a concrete user.

    Not the human's online status — it is what Nodya is currently
    busy with in this conversation: thinking over a message, free,
    or sleeping (after consolidation).
    """

    status: Literal["idle", "thinking", "sleeping"]
    last_active_at: datetime


class ContextMessage(BaseModel):
    """A single entry of the short-term dialogue history.

    role "summary" holds the compressed memory of earlier
    conversations produced by Consolidation; it survives context
    replacement and is rendered as a system block in prompts.
    """

    role: Literal["user", "assistant", "summary"]
    content: str
    timestamp: datetime


class RedisClient:
    """Wrapper over redis.asyncio.Redis for short-term memory.

    Composition instead of inheritance on purpose: otherwise every
    low-level client method surfaces on the instance next to domain
    methods like get_state/acquire_lock.
    """

    def __init__(self, redis_url: str) -> None:
        """Create pools and register Lua scripts.

        Args:
            redis_url: redis:// DSN (decode_responses enabled).
        """
        self._redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self._release_lock_script = self._redis.register_script(
            _RELEASE_LOCK_SCRIPT
        )
        self._pop_due_script = self._redis.register_script(_POP_DUE_SCRIPT)

    async def close(self) -> None:
        """Close the connection pool; call on graceful shutdown."""
        await self._redis.aclose()

    # --- Telegram pairing codes (L1) ---

    async def issue_link_code(self, user_id: UUID) -> str:
        """Issue a one-time Telegram pairing code for a user.

        A new code invalidates the previous pending one of the same
        user; the code lives LINK_TTL_SECONDS and is consumed by
        consume_link_code exactly once (GETDEL).

        Args:
            user_id: Account that will receive the telegram binding.

        Returns:
            8-character code from an unambiguous alphabet.
        """
        pending_key = f"{_LINK_PREFIX}:user:{user_id}"
        previous = await self._redis.get(pending_key)
        if previous:
            await self._redis.delete(f"{_LINK_PREFIX}:{previous}")
        code = "".join(secrets.choice(_LINK_ALPHABET) for _ in range(8))
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(
                f"{_LINK_PREFIX}:{code}", str(user_id), ex=LINK_TTL_SECONDS
            )
            pipe.set(pending_key, code, ex=LINK_TTL_SECONDS)
            await pipe.execute()
        return code

    async def consume_link_code(self, code: str) -> UUID | None:
        """Consume a pairing code atomically (one-time use).

        Args:
            code: Code supplied by the user in Telegram.

        Returns:
            Bound account UUID, or None when unknown/expired.
        """
        raw = await self._redis.getdel(
            f"{_LINK_PREFIX}:{code.strip().upper()}"
        )
        return UUID(raw) if raw else None

    async def rename_user_keys(
        self, old_user_id: UUID, new_user_id: UUID
    ) -> None:
        """Move short-term memory keys after an account merge.

        Context and state are RENAMEd onto the surviving account;
        transient lock/debounce keys of the removed identity are
        simply dropped.

        Args:
            old_user_id: Identity being absorbed.
            new_user_id: Surviving identity.
        """
        for old_key, new_key in (
            (_context_key(old_user_id), _context_key(new_user_id)),
            (_state_key(old_user_id), _state_key(new_user_id)),
        ):
            if await self._redis.exists(old_key):
                await self._redis.rename(old_key, new_key)
        await self._redis.delete(
            _lock_key(old_user_id), _debounce_key(old_user_id)
        )

    async def ping(self) -> bool:
        """Check Redis availability for health/fail-fast probes.

        Returns:
            True when PING succeeds, False on any error.
        """
        try:
            return bool(await self._redis.ping())
        except Exception as exc:
            logger.warning(f"Redis unreachable: {exc}")
            return False

    # --- Scheduled (ADR-13/15) ---

    async def push_scheduled(self, member: str, score_ts: float) -> None:
        """Add an entry to the scheduled-tasks ZSet.

        Args:
            member: Serialized task (ScheduledEnvelope JSON).
            score_ts: Unix timestamp when the task becomes due.
        """
        await self._redis.zadd(_SCHEDULED_KEY, {member: score_ts})

    async def pop_due_scheduled(
        self, now_ts: float, limit: int = 50
    ) -> list[str]:
        """Atomically fetch due entries from the ZSet.

        Fetch and removal run in one Lua script: without it another
        worker could grab the same entries between read and delete.

        Args:
            now_ts: Current unix timestamp.
            limit: Max entries to pop per call.

        Returns:
            Raw serialized entries with score <= now_ts.
        """
        raw_items = await self._pop_due_script(
            keys=[_SCHEDULED_KEY], args=[now_ts, limit]
        )
        return [str(item) for item in raw_items]

    # --- State ---

    async def get_state(self, user_id: UUID) -> DialogueState | None:
        """Return the dialogue state for a user, or None.

        Args:
            user_id: Internal user UUID.

        Returns:
            Parsed DialogueState, or None when no hash exists.
        """
        data = await self._redis.hgetall(_state_key(user_id))
        if not data:
            return None
        return DialogueState(
            status=data["status"],
            last_active_at=datetime.fromisoformat(data["last_active_at"]),
        )

    async def set_state(
        self,
        user_id: UUID,
        status: Literal["idle", "thinking", "sleeping"],
        ttl: int | None = None,
    ) -> None:
        """Persist the dialogue status, optionally expiring the key.

        Args:
            user_id: Internal user UUID.
            status: New dialogue status.
            ttl: Optional TTL seconds (guards against stuck states).
        """
        key = _state_key(user_id)
        now = datetime.now(UTC).isoformat()
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping={"status": status, "last_active_at": now})
            if ttl is not None:
                pipe.expire(key, ttl)
            await pipe.execute()

    async def get_state_field(self, user_id: UUID, field: str) -> str | None:
        """Read a single field of the state hash.

        Args:
            user_id: Internal user UUID.
            field: Hash field name.

        Returns:
            Field value or None when absent.
        """
        return await self._redis.hget(_state_key(user_id), field)

    # --- Context ---

    async def push_context_many(
        self, user_id: UUID, messages: list[ContextMessage]
    ) -> None:
        """Append messages to history in one transaction.

        RPUSH all items + one LTRIM to the cap + one EXPIRE: N
        messages cost one round-trip, not N.

        Args:
            user_id: Internal user UUID.
            messages: Ordered messages to append.
        """
        if not messages:
            return
        key = _context_key(user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            for message in messages:
                pipe.rpush(key, message.model_dump_json())
            pipe.ltrim(key, -_CONTEXT_MAX_LEN, -1)
            pipe.expire(key, _CONTEXT_TTL_SECONDS)
            await pipe.execute()

    async def push_context(
        self, user_id: UUID, message: ContextMessage
    ) -> None:
        """Append a single message (convenience over push_context_many).

        Args:
            user_id: Internal user UUID.
            message: Message to append.
        """
        await self.push_context_many(user_id, [message])

    async def get_context(
        self, user_id: UUID, limit: int = 20
    ) -> list[ContextMessage]:
        """Return the last `limit` messages in chronological order.

        Args:
            user_id: Internal user UUID.
            limit: How many recent messages to return.

        Returns:
            Chronologically ordered messages; damaged records are
            skipped with a warning.
        """
        raw_items = await self._redis.lrange(_context_key(user_id), -limit, -1)
        messages: list[ContextMessage] = []
        for raw in raw_items:
            try:
                messages.append(ContextMessage.model_validate_json(raw))
            except ValueError:
                logger.warning(f"Damaged context record: user={user_id}.")
        return messages

    async def replace_context(
        self, user_id: UUID, messages: list[ContextMessage]
    ) -> None:
        """Atomically swap the whole history (Consolidation sleep).

        DELETE + RPUSH + EXPIRE run in one transaction: a concurrent
        reader sees either the old history or the compressed one,
        never an empty gap.

        Args:
            user_id: Internal user UUID.
            messages: New history entries (typically one summary).
        """
        key = _context_key(user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            for message in messages:
                pipe.rpush(key, message.model_dump_json())
            pipe.expire(key, _CONTEXT_TTL_SECONDS)
            await pipe.execute()

    async def clear_context(self, user_id: UUID) -> None:
        """Drop the whole dialogue history.

        Args:
            user_id: Internal user UUID.
        """
        await self._redis.delete(_context_key(user_id))

    async def context_length(self, user_id: UUID) -> int:
        """Current number of entries in the dialogue history.

        Args:
            user_id: Internal user UUID.

        Returns:
            LLEN of the context list.
        """
        return int(await self._redis.llen(_context_key(user_id)))

    # --- Lock ---

    async def acquire_lock(self, user_id: UUID, ttl: int = 30) -> str | None:
        """Try to acquire the user lock.

        Args:
            user_id: Internal user UUID.
            ttl: Lock expiry seconds (crash safety).

        Returns:
            Unique owner token on success, None when already locked.
            The token MUST be passed back to release_lock.
        """
        token = uuid4().hex
        acquired = await self._redis.set(
            _lock_key(user_id), token, nx=True, ex=ttl
        )
        return token if acquired else None

    async def release_lock(self, user_id: UUID, token: str) -> bool:
        """Release the lock only if this token still owns it.

        Atomic via Lua script: check-then-delete without it races the
        TTL expiry (we could delete someone else's fresh lock).

        Args:
            user_id: Internal user UUID.
            token: Token returned by acquire_lock.

        Returns:
            True when the lock was released by this call.
        """
        result = await self._release_lock_script(
            keys=[_lock_key(user_id)], args=[token]
        )
        return bool(result)

    async def is_locked(self, user_id: UUID) -> bool:
        """Whether the lock is held right now.

        Args:
            user_id: Internal user UUID.

        Returns:
            True when the lock key exists.
        """
        return bool(await self._redis.exists(_lock_key(user_id)))

    # --- Debounce ---

    async def push_debounce(self, user_id: UUID, text: str) -> int:
        """Append text to the debounce buffer.

        The silence timer itself belongs to calling code (Worker);
        the TTL here is only a safety net when pop never happens.

        Args:
            user_id: Internal user UUID.
            text: Raw message text.

        Returns:
            Current buffer size after the push.
        """
        key = _debounce_key(user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, text)
            pipe.expire(key, _DEBOUNCE_SAFETY_TTL_SECONDS)
            size, _ = await pipe.execute()
        return size

    async def pop_debounce_batch(self, user_id: UUID) -> list[str]:
        """Atomically drain the debounce buffer.

        Args:
            user_id: Internal user UUID.

        Returns:
            All buffered texts; the key is deleted afterwards.
        """
        key = _debounce_key(user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            batch, _ = await pipe.execute()
        return batch

    # --- Nodya Agent presence ---

    async def set_agent_online(self, user_id: UUID, ttl: int = 15) -> None:
        """Refresh the agent presence key (called by heartbeats).

        Args:
            user_id: Owner of the agent process.
            ttl: Presence expiry seconds.
        """
        await self._redis.set(_agent_online_key(user_id), "1", ex=ttl)

    async def is_agent_online(self, user_id: UUID) -> bool:
        """Whether the agent heartbeat is fresh.

        Args:
            user_id: Owner of the agent process.

        Returns:
            True when the presence key exists.
        """
        return bool(await self._redis.exists(_agent_online_key(user_id)))
