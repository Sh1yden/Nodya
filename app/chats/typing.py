"""Channel-agnostic typing indicators (ADR-17).

Worker publishes ``TypingEvent`` (start/stop) via RabbitMQ;
senders consume and delegate to channel-specific handlers.

Telegram: loop ``send_chat_action("typing")`` every 4s (expires in 5s).
Browser/Discord: no-op for now, but interface is ready for WS
``{"type":"typing"}`` or Discord ``triggerTyping``.

No sender imports Worker; no Worker imports aiogram.
"""

import asyncio
from abc import ABC, abstractmethod
from contextlib import suppress
from typing import Final

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.core import LoggerMixin, get_logger

logger = get_logger(__name__)

_TYPING_INTERVAL_SECONDS: Final = 4.0


class TypingIndicator(ABC):
    """Abstract typing handler for a single channel."""

    @abstractmethod
    async def start(self, chat_id: int) -> None:
        """Show typing indicator (may start a loop)."""

    @abstractmethod
    async def stop(self, chat_id: int) -> None:
        """Hide typing indicator (stop the loop)."""


class TelegramTypingIndicator(LoggerMixin, TypingIndicator):
    """Telegram typing via ``send_chat_action("typing")`` loop."""

    def __init__(self, bot: Bot) -> None:
        """Store bot, no I/O.

        Args:
            bot: aiogram Bot used for ``send_chat_action``.
        """
        self._bot = bot
        self._tasks: dict[int, asyncio.Task] = {}

    async def start(self, chat_id: int) -> None:
        """Start typing loop for chat.

        Args:
            chat_id: Telegram chat id.
        """
        if chat_id in self._tasks:
            return
        self._tasks[chat_id] = asyncio.create_task(
            self._loop(chat_id), name=f"typing:{chat_id}"
        )
        self._lg.debug(f"Typing started for {chat_id}.")

    async def stop(self, chat_id: int) -> None:
        """Stop typing loop for chat.

        Args:
            chat_id: Telegram chat id.
        """
        task = self._tasks.pop(chat_id, None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._lg.debug(f"Typing stopped for {chat_id}.")

    async def stop_all(self) -> None:
        """Stop all active typing loops."""
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, chat_id: int) -> None:
        """Send typing every 4s until cancelled.

        Args:
            chat_id: Telegram chat id.
        """
        with suppress(asyncio.CancelledError):
            while True:
                try:
                    await self._bot.send_chat_action(
                        chat_id=chat_id, action="typing"
                    )
                except TelegramAPIError as exc:
                    logger.warning(f"Typing failed for {chat_id}: {exc}")
                await asyncio.sleep(_TYPING_INTERVAL_SECONDS)


class NoOpTypingIndicator(TypingIndicator):
    """No-op for channels without typing."""

    async def start(self, chat_id: int) -> None:
        """No-op."""

    async def stop(self, chat_id: int) -> None:
        """No-op."""
