"""Integration tests for ``app.chats.telegram.sender``."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.common.schemas import OutgoingMessage


def _make_session_factory(
    session: AsyncMock,
) -> AsyncGenerator:
    """Return a factory that yields the given session."""

    @asynccontextmanager
    async def _factory() -> AsyncGenerator:
        yield session

    return _factory


class TestTGSender:
    def test_run_raises_on_empty_token(self) -> None:
        from app.chats.telegram.sender import TGSender

        sender = TGSender(
            broker_url="amqp://guest:guest@localhost/",
            bot_token="",
        )
        with pytest.raises(RuntimeError, match="empty"):
            import asyncio

            asyncio.run(sender.run())

    async def test_on_message_skips_non_telegram(self) -> None:
        from app.chats.telegram.sender import TGSender

        sender = TGSender.__new__(TGSender)
        object.__setattr__(sender, "_logger", Mock())
        sender._bot = AsyncMock()
        sender._session_factory = AsyncMock()

        msg = AsyncMock()
        payload = OutgoingMessage(
            user_id=uuid.uuid4(),
            channel="discord",
            text="Hello",
        )
        msg.body = payload.model_dump_json().encode()

        await sender._on_message(msg)
        msg.ack.assert_awaited_once()
        sender._bot.send_message.assert_not_awaited()

    async def test_on_message_sends_telegram(self) -> None:
        from app.chats.telegram.sender import TGSender

        uid = uuid.uuid4()
        sender = TGSender.__new__(TGSender)
        object.__setattr__(sender, "_logger", Mock())
        sender._bot = AsyncMock()

        session = AsyncMock()
        sender._session_factory = _make_session_factory(session)

        user = Mock()
        user.telegram_id = 12345
        repo = AsyncMock()
        repo.get_by_id = AsyncMock(return_value=user)

        with patch(
            "app.chats.telegram.sender.UsersRepo",
            return_value=repo,
        ):
            msg = AsyncMock()
            payload = OutgoingMessage(
                user_id=uid,
                channel="telegram",
                text="Reply here",
            )
            msg.body = payload.model_dump_json().encode()

            await sender._on_message(msg)
            sender._bot.send_message.assert_awaited_once()
            msg.ack.assert_awaited_once()
