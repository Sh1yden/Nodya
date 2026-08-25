"""Доставка исходящих сообщений в Telegram.

Consumer очереди outgoing_messages с фильтром channel == "telegram".
Отправка через aiogram Bot; ретраи только на сетевые/рейт-ошибки,
после 3 неудач — nack в DLQ (Этап 6.2 TODO_MASTER).
"""

import asyncio
from uuid import UUID

import aio_pika
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustQueue,
)
from aiogram import Bot
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
)

from app.brain.memory.long import AsyncSessionLocal
from app.brain.repositories import UsersRepo
from app.common import (
    OutgoingMessage,
    declare_outgoing_queue,
    declare_topology,
)
from app.core import LoggerMixin

_PREFETCH_COUNT = 20
_MAX_SEND_ATTEMPTS = 3


class TGSender(LoggerMixin):
    """Channel Sender для telegram (Этап 6.2)."""

    def __init__(
        self,
        broker_url: str,
        bot_token: str,
        session_factory: type = AsyncSessionLocal,
    ) -> None:
        self._url = broker_url
        self._bot_token = bot_token
        self._session_factory = session_factory
        self._bot: Bot | None = None
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._queue: AbstractRobustQueue | None = None
        self._running = False

    async def run(self) -> None:
        """Подключиться к брокеру и начать consume outgoing."""
        if not self._bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN пуст.")
        self._bot = Bot(token=self._bot_token)
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=_PREFETCH_COUNT)
        exchange = await declare_topology(self._channel)
        self._queue = await declare_outgoing_queue(self._channel, exchange)
        self._running = True
        await self._queue.consume(self._on_message)

    async def stop(self) -> None:
        """Graceful shutdown: consumer, сессия бота, брокер."""
        self._running = False
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        if self._bot is not None:
            await self._bot.session.close()

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        """Фильтр по каналу, отправка, ACK/NACK."""
        try:
            dto = OutgoingMessage.model_validate_json(message.body)
        except ValueError:
            await message.nack(requeue=False)
            return
        if dto.channel != "telegram":
            await message.ack()
            return

        chat_id = await self._lookup_chat_id(dto.user_id)
        if chat_id is None:
            self._lg.warning(
                "telegram_id не найден для user_id=%s — сообщение "
                "не доставлено.",
                dto.user_id,
            )
            await message.ack()
            return

        assert self._bot is not None
        reason: str
        for attempt in range(_MAX_SEND_ATTEMPTS):
            try:
                await self._bot.send_message(chat_id=chat_id, text=dto.text)
                self._lg.info(
                    "Доставлено в Telegram: chat_id=%s, len=%d.",
                    chat_id,
                    len(dto.text),
                )
                await message.ack()
                return
            except TelegramRetryAfter as exc:
                delay = float(exc.retry_after)
                reason = type(exc).__name__
            except TelegramNetworkError as exc:
                delay = 0.5 * (2**attempt)
                reason = type(exc).__name__
            self._lg.warning(
                "Попытка %d/%d не удалась (%s), повтор через %.1fs.",
                attempt + 1,
                _MAX_SEND_ATTEMPTS,
                reason,
                delay,
            )
            await asyncio.sleep(delay)

        self._lg.error(
            "Не доставлено после %s попыток user_id=%s — DLQ.",
            _MAX_SEND_ATTEMPTS,
            dto.user_id,
        )
        await message.nack(requeue=False)

    async def _lookup_chat_id(self, user_id: UUID) -> int | None:
        """telegram_id пользователя для доставки."""
        async with self._session_factory() as session:
            repo = UsersRepo(session)
            user = await repo.get_by_id(user_id)
            if user is None or user.telegram_id is None:
                return None
            return int(user.telegram_id)
