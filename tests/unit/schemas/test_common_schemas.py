"""Unit tests for ``app.common.schemas``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.common.schemas import (
    IncomingMessage,
    OutgoingMessage,
    ScheduledEnvelope,
)


class TestIncomingMessage:
    """Validate IncomingMessage construction."""

    def test_valid_construction(self) -> None:
        msg = IncomingMessage(
            user_external_id="123456",
            channel="telegram",
            text="Hello",
            received_at=datetime.now(UTC),
        )
        assert msg.channel == "telegram"
        assert msg.text == "Hello"

    def test_invalid_channel_raises(self) -> None:
        with pytest.raises(ValidationError):
            IncomingMessage(
                user_external_id="1",
                channel="invalid",  # type: ignore[arg-type]
                text="x",
                received_at=datetime.now(UTC),
            )

    def test_all_channels_accepted(self) -> None:
        for ch in ("telegram", "discord", "browser", "cli"):
            msg = IncomingMessage(
                user_external_id="1",
                channel=ch,  # type: ignore[arg-type]
                text="x",
                received_at=datetime.now(UTC),
            )
            assert msg.channel == ch

    def test_identity_fields_default_to_none(self) -> None:
        msg = IncomingMessage(
            user_external_id="1",
            channel="telegram",
            text="x",
            received_at=datetime.now(UTC),
        )
        assert msg.chat_external_id is None
        assert msg.sender_name is None
        assert msg.sender_username is None

    def test_identity_fields_accepted(self) -> None:
        msg = IncomingMessage(
            user_external_id="111",
            chat_external_id="-100500",
            sender_name="Иван",
            sender_username="ivan",
            channel="telegram",
            text="x",
            received_at=datetime.now(UTC),
        )
        assert msg.chat_external_id == "-100500"
        assert msg.sender_name == "Иван"
        assert msg.sender_username == "ivan"


class TestOutgoingMessage:
    """Validate OutgoingMessage construction."""

    def test_valid_construction(self) -> None:
        msg = OutgoingMessage(
            user_id=uuid4(),
            channel="telegram",
            text="Reply",
        )
        assert msg.delay_until is None

    def test_with_delay(self) -> None:
        now = datetime.now(UTC)
        msg = OutgoingMessage(
            user_id=uuid4(),
            channel="telegram",
            text="Later",
            delay_until=now,
        )
        assert msg.delay_until == now

    def test_with_pinned_chat(self) -> None:
        msg = OutgoingMessage(
            user_id=uuid4(),
            channel="telegram",
            text="Group reply",
            chat_external_id="-100500",
        )
        assert msg.chat_external_id == "-100500"


class TestScheduledEnvelope:
    """Validate ScheduledEnvelope construction."""

    def test_incoming_kind(self) -> None:
        env = ScheduledEnvelope(
            kind="incoming",
            incoming=[
                IncomingMessage(
                    user_external_id="1",
                    channel="telegram",
                    text="hi",
                    received_at=datetime.now(UTC),
                )
            ],
        )
        assert env.kind == "incoming"
        assert env.retry_count == 0

    def test_outgoing_kind(self) -> None:
        env = ScheduledEnvelope(
            kind="outgoing",
            outgoing=OutgoingMessage(
                user_id=uuid4(),
                channel="telegram",
                text="reply",
            ),
        )
        assert env.kind == "outgoing"

    def test_kind_literal_validation(self) -> None:
        with pytest.raises(ValidationError):
            ScheduledEnvelope(kind="invalid")  # type: ignore[arg-type]
