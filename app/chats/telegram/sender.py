"""Delivery of outgoing messages to Telegram.

Consumer of the outgoing_messages queue filtered by
channel == "telegram". Sends via aiogram Bot; retries only network /
rate-limit errors, after 3 failures — nack into DLQ (TODO_MASTER
6.2).
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
    """Channel sender for the telegram channel (TODO_MASTER 6.2)."""

    def __init__(
        self,
        broker_url: str,
        bot_token: str,
        session_factory: type = AsyncSessionLocal,
    ) -> None:
        """Store connection parameters (no I/O here).

        Args:
            broker_url: AMQP DSN for the outgoing consumer.
            bot_token: Telegram bot token for sending.
            session_factory: Async session factory for id lookups.
        """
        self._url = broker_url
        self._bot_token = bot_token
        self._session_factory = session_factory
        self._bot: Bot | None = None
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._queue: AbstractRobustQueue | None = None
        self._running = False

    async def run(self) -> None:
        """Connect to the broker and start consuming outgoing."""
        if not self._bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is empty.")
        self._lg.debug("TGSender starting...")
        self._bot = Bot(token=self._bot_token)
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=_PREFETCH_COUNT)
        exchange = await declare_topology(self._channel)
        self._queue = await declare_outgoing_queue(self._channel, exchange)
        self._running = True
        await self._queue.consume(self._on_message)

    async def stop(self) -> None:
        """Graceful shutdown: consumer, bot session, broker."""
        self._running = False
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        if self._bot is not None:
            await self._bot.session.close()
        self._lg.debug("TGSender stopped.")

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        """Filter by channel, deliver, then ACK or NACK.

        Args:
            message: Raw AMQP delivery from outgoing_messages.
        """
        try:
            dto = OutgoingMessage.model_validate_json(message.body)
        except ValueError:
            self._lg.warning("Malformed payload in outgoing queue.")
            await message.nack(requeue=False)
            return
        if dto.channel != "telegram":
            self._lg.debug(f"Skipping non-telegram message {dto.user_id}.")
            await message.ack()
            return

        chat_id = await self._lookup_chat_id(dto.user_id)
        if chat_id is None:
            self._lg.warning(
                f"telegram_id missing for user_id={dto.user_id} — "
                "message dropped."
            )
            await message.ack()
            return

        assert self._bot is not None
        reason: str
        for attempt in range(_MAX_SEND_ATTEMPTS):
            try:
                await self._bot.send_message(chat_id=chat_id, text=dto.text)
                self._lg.info(
                    f"Delivered to Telegram: chat_id={chat_id}, "
                    f"len={len(dto.text)}."
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
                f"Attempt {attempt + 1}/{_MAX_SEND_ATTEMPTS} failed "
                f"({reason}), retrying in {delay:.1f}s."
            )
            await asyncio.sleep(delay)

        self._lg.error(
            f"Not delivered after {_MAX_SEND_ATTEMPTS} attempts, "
            f"user_id={dto.user_id} — DLQ."
        )
        await message.nack(requeue=False)

    async def _lookup_chat_id(self, user_id: UUID) -> int | None:
        """Resolve the telegram_id of a user.

        Args:
            user_id: Internal user UUID.

        Returns:
            telegram_id, or None when the user/channel is unknown.
        """
        async with self._session_factory() as session:
            repo = UsersRepo(session)
            user = await repo.get_by_id(user_id)
            if user is None or user.telegram_id is None:
                return None
            return int(user.telegram_id)
