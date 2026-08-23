"""Short-term память Nodya поверх Redis.

Единая точка входа для работы с state/context/lock/debounce/presence.
Остальной код никогда не работает с redis-py напрямую — только через
RedisClient.

Путь в проекте: app/brain/memory/short/redis.py
"""

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


def _state_key(user_id: UUID) -> str:
    return f"{_STATE_PREFIX}:{user_id}"


def _context_key(user_id: UUID) -> str:
    return f"{_CONTEXT_PREFIX}:{user_id}"


def _lock_key(user_id: UUID) -> str:
    return f"{_LOCK_PREFIX}:{user_id}"


def _debounce_key(user_id: UUID) -> str:
    return f"{_DEBOUNCE_PREFIX}:{user_id}"


def _agent_online_key(user_id: UUID) -> str:
    return f"{_AGENT_ONLINE_PREFIX}:{user_id}"


class DialogueState(BaseModel):
    """Состояние Ноди по отношению к диалогу с конкретным user_id.

    Не статус самого человека (онлайн/офлайн и т.п.) — это то, чем
    сейчас занята Нодя в рамках именно этой переписки: думает над
    сообщением, свободна или "спит" (после Consolidation). Один и
    тот же user_id тут — не про юзера, а про то, какой диалог Нодя
    сейчас обслуживает.
    """

    status: Literal["idle", "thinking", "sleeping"]
    last_active_at: datetime


class ContextMessage(BaseModel):
    """Одно сообщение в истории диалога (short-term контекст)."""

    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime


class RedisClient:
    """Обёртка над redis.asyncio.Redis для short-term памяти.

    Намеренно через композицию, а не наследование от Redis — иначе
    на инстансе всплывают все низкоуровневые методы клиента вперемешку
    с доменными (get_state, acquire_lock и т.д.), и непонятно, каким
    можно пользоваться снаружи.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis: Redis = Redis.from_url(redis_url, decode_responses=True)
        self._release_lock_script = self._redis.register_script(
            _RELEASE_LOCK_SCRIPT
        )

    async def close(self) -> None:
        """Закрыть пул соединений. Вызывать при graceful shutdown."""
        await self._redis.aclose()

    # --- State ---

    async def get_state(self, user_id: UUID) -> DialogueState | None:
        """Вернуть текущее состояние диалога с этим user_id, либо None."""
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
        """Записать статус диалога с этим user_id, опционально с TTL."""
        key = _state_key(user_id)
        now = datetime.now(UTC).isoformat()
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping={"status": status, "last_active_at": now})
            if ttl is not None:
                pipe.expire(key, ttl)
            await pipe.execute()

    async def get_state_field(self, user_id: UUID, field: str) -> str | None:
        """Получить одно поле state-хэша без чтения всего объекта."""
        return await self._redis.hget(_state_key(user_id), field)

    # --- Context ---

    async def push_context(
        self, user_id: UUID, message: ContextMessage
    ) -> None:
        """Добавить сообщение в историю, обрезать до лимита, обновить TTL."""
        key = _context_key(user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, message.model_dump_json())
            pipe.ltrim(key, -_CONTEXT_MAX_LEN, -1)
            pipe.expire(key, _CONTEXT_TTL_SECONDS)
            await pipe.execute()

    async def get_context(
        self, user_id: UUID, limit: int = 20
    ) -> list[ContextMessage]:
        """Вернуть последние `limit` сообщений в хронологическом порядке."""
        raw_items = await self._redis.lrange(_context_key(user_id), -limit, -1)
        messages: list[ContextMessage] = []
        for raw in raw_items:
            try:
                messages.append(ContextMessage.model_validate_json(raw))
            except ValueError:
                logger.warning(
                    "Повреждённая запись контекста user_id=%s: %r",
                    user_id,
                    raw,
                )
        return messages

    async def clear_context(self, user_id: UUID) -> None:
        """Удалить всю историю диалога (используется Consolidation)."""
        await self._redis.delete(_context_key(user_id))

    # --- Lock ---

    async def acquire_lock(self, user_id: UUID, ttl: int = 30) -> str | None:
        """Захватить лок пользователя.

        Возвращает уникальный токен владения при успехе, либо None,
        если лок уже занят. Токен обязателен передать в release_lock —
        иначе можно случайно снять чужой лок после гонки по TTL.
        """
        token = uuid4().hex
        acquired = await self._redis.set(
            _lock_key(user_id), token, nx=True, ex=ttl
        )
        return token if acquired else None

    async def release_lock(self, user_id: UUID, token: str) -> bool:
        """Снять лок, только если он всё ещё принадлежит этому token.

        Атомарно через Lua-скрипт: без него между проверкой владельца
        и удалением ключа возможна гонка (TTL истёк, лок перехвачен
        другим воркером — мы бы удалили чужой, свежий лок).
        """
        result = await self._release_lock_script(
            keys=[_lock_key(user_id)], args=[token]
        )
        return bool(result)

    async def is_locked(self, user_id: UUID) -> bool:
        """Проверить, занят ли лок пользователя прямо сейчас."""
        return bool(await self._redis.exists(_lock_key(user_id)))

    # --- Debounce ---

    async def push_debounce(self, user_id: UUID, text: str) -> int:
        """Добавить сообщение в буфер, вернуть текущий размер буфера.

        Таймер задержки (5с тишины) — ответственность вызывающего
        кода (Worker/API Gateway), здесь только хранение сообщений.
        TTL на ключе — не сама логика debounce, а страховка на
        случай, если pop так и не будет вызван.
        """
        key = _debounce_key(user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, text)
            pipe.expire(key, _DEBOUNCE_SAFETY_TTL_SECONDS)
            size, _ = await pipe.execute()
        return size

    async def pop_debounce_batch(self, user_id: UUID) -> list[str]:
        """Атомарно забрать и очистить буфер отложенных сообщений."""
        key = _debounce_key(user_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            batch, _ = await pipe.execute()
        return batch

    # --- Nodya Agent presence ---

    async def set_agent_online(self, user_id: UUID, ttl: int = 15) -> None:
        """Обновить presence-ключ Nodya Agent (вызывается heartbeat'ом)."""
        await self._redis.set(_agent_online_key(user_id), "1", ex=ttl)

    async def is_agent_online(self, user_id: UUID) -> bool:
        """Проверить, онлайн ли Nodya Agent владельца прямо сейчас."""
        return bool(await self._redis.exists(_agent_online_key(user_id)))
