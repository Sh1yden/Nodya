"""Unit tests for ``app.common.schemas``."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.common.schemas import (
    IncomingMessage,
    MediaDescriptor,
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

    def test_legacy_media_payload_still_valid(self) -> None:
        msg = IncomingMessage(
            user_external_id="1",
            channel="telegram",
            text="смотри",
            received_at=datetime.now(UTC),
            media_type="photo",
            media_file_id="file-1",
            caption="подпись",
        )
        assert msg.media is None
        assert msg.media_type == "photo"
        assert msg.media_file_id == "file-1"
        assert msg.caption == "подпись"

    def test_media_descriptor_payload(self) -> None:
        media = MediaDescriptor(
            kind="sticker",
            file_id="sticker-1",
            thumbnail_file_id="thumb-1",
            emoji="😂",
            sticker_format="animated",
            sticker_set_name="cats",
        )
        msg = IncomingMessage(
            user_external_id="1",
            channel="telegram",
            text="",
            received_at=datetime.now(UTC),
            media=media,
            media_type="sticker",
            media_file_id="sticker-1",
        )
        assert msg.media == media
        assert msg.media_type == "sticker"


class TestMediaDescriptor:
    """Validate the channel-agnostic media descriptor."""

    def test_file_id_and_kind_required(self) -> None:
        with pytest.raises(ValidationError):
            MediaDescriptor(kind="photo")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            MediaDescriptor(file_id="f")  # type: ignore[call-arg]

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MediaDescriptor(kind="document", file_id="f")  # type: ignore[arg-type]

    def test_all_kinds_accepted(self) -> None:
        kinds = (
            "photo",
            "video",
            "voice",
            "audio",
            "animation",
            "sticker",
            "video_note",
        )
        for kind in kinds:
            media = MediaDescriptor(kind=kind, file_id=f"id-{kind}")
            assert media.kind == kind

    def test_round_trip(self) -> None:
        media = MediaDescriptor(
            kind="video",
            file_id="video-1",
            thumbnail_file_id="thumb-1",
            mime_type="video/mp4",
            width=1280,
            height=720,
            duration_seconds=12,
            file_size=1024,
            file_name="clip.mp4",
        )
        restored = MediaDescriptor.model_validate(media.model_dump())
        assert restored == media


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
