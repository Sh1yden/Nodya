"""Integration tests for ``app.chats.telegram.webhook``."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Update
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chats.telegram.webhook import _extract_message, router


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _make_telegram_update(
    chat_id: int = 12345,
    text: str = "Hello bot",
    message_id: int = 1,
    sender_id: int | None = None,
    chat_type: str = "private",
    first_name: str | None = "Test",
    username: str | None = "testuser",
) -> dict:
    """Build a realistic Telegram update payload."""
    sender = {
        "id": sender_id if sender_id is not None else chat_id,
        "is_bot": False,
    }
    if first_name is not None:
        sender["first_name"] = first_name
    if username is not None:
        sender["username"] = username
    return {
        "update_id": 1,
        "message": {
            "message_id": message_id,
            "from": sender,
            "chat": {
                "id": chat_id,
                "type": chat_type,
            },
            "date": 1234567890,
            "text": text,
        },
    }


def _photo_size(file_id: str, width: int, height: int) -> dict:
    """Build a Telegram PhotoSize payload."""
    return {
        "file_id": file_id,
        "file_unique_id": f"uniq-{file_id}",
        "width": width,
        "height": height,
        "file_size": width * height,
    }


def _media_update(
    *,
    chat_id: int = 12345,
    caption: str | None = None,
    **media: object,
) -> dict:
    """Build an update whose message carries media instead of text."""
    message: dict = {
        "message_id": 2,
        "from": {
            "id": chat_id,
            "is_bot": False,
            "first_name": "Test",
            "username": "testuser",
        },
        "chat": {"id": chat_id, "type": "private"},
        "date": 1234567890,
        **media,
    }
    if caption is not None:
        message["caption"] = caption
    return {"update_id": 2, "message": message}


def _extract(payload: dict):
    """Parse an update payload the same way the webhook does."""
    return _extract_message(Update.model_validate(payload))


class TestTelegramWebhook:
    def test_wrong_secret_returns_403(self, client: TestClient) -> None:
        with patch("app.chats.telegram.webhook.settings") as mock_settings:
            mock_settings.TELEGRAM_WEBHOOK_SECRET = "real-secret"
            response = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
                json={"update_id": 1},
            )
            assert response.status_code == 403

    def test_valid_secret_accepted(
        self, client: TestClient, app: FastAPI
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_incoming = AsyncMock()
        app.state.publisher = mock_publisher

        with patch("app.chats.telegram.webhook.settings") as mock_settings:
            mock_settings.TELEGRAM_WEBHOOK_SECRET = "secret123"

            payload = _make_telegram_update(chat_id=12345, text="Hello bot")
            response = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
                json=payload,
            )
            assert response.status_code == 202

    def test_no_message_returns_ignored(
        self, client: TestClient, app: FastAPI
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_incoming = AsyncMock()
        app.state.publisher = mock_publisher

        with patch("app.chats.telegram.webhook.settings") as mock_settings:
            mock_settings.TELEGRAM_WEBHOOK_SECRET = "secret123"

            response = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
                json={"update_id": 2},
            )
            # Route default status_code is 202
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "ignored"

    def test_empty_text_returns_ignored(
        self, client: TestClient, app: FastAPI
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_incoming = AsyncMock()
        app.state.publisher = mock_publisher

        with patch("app.chats.telegram.webhook.settings") as mock_settings:
            mock_settings.TELEGRAM_WEBHOOK_SECRET = "secret123"

            payload = _make_telegram_update(chat_id=12345, text="")
            response = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
                json=payload,
            )
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "ignored"

    def test_private_chat_publishes_profile(
        self, client: TestClient, app: FastAPI
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_incoming = AsyncMock()
        app.state.publisher = mock_publisher

        with patch("app.chats.telegram.webhook.settings") as mock_settings:
            mock_settings.TELEGRAM_WEBHOOK_SECRET = "secret123"

            payload = _make_telegram_update(
                chat_id=111,
                text="привет",
                first_name="Иван",
                username="ivan",
            )
            response = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
                json=payload,
            )
            assert response.status_code == 202

        msg = mock_publisher.publish_incoming.await_args.args[0]
        assert msg.user_external_id == "111"
        assert msg.chat_external_id == "111"
        assert msg.sender_name == "Иван"
        assert msg.sender_username == "ivan"

    def test_group_uses_sender_identity_and_chat_destination(
        self, client: TestClient, app: FastAPI
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_incoming = AsyncMock()
        app.state.publisher = mock_publisher

        with patch("app.chats.telegram.webhook.settings") as mock_settings:
            mock_settings.TELEGRAM_WEBHOOK_SECRET = "secret123"

            payload = _make_telegram_update(
                chat_id=-100500,
                sender_id=111,
                chat_type="group",
                text="привет",
            )
            response = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
                json=payload,
            )
            assert response.status_code == 202

        msg = mock_publisher.publish_incoming.await_args.args[0]
        assert msg.user_external_id == "111"
        assert msg.chat_external_id == "-100500"


class TestTelegramMediaNormalization:
    """Telegram media is normalized into a channel-agnostic descriptor."""

    def test_photo_prefers_largest_size_within_limit(self) -> None:
        parsed = _extract(
            _media_update(
                photo=[
                    _photo_size("small", 90, 90),
                    _photo_size("medium", 800, 600),
                    _photo_size("limit", 1280, 960),
                    _photo_size("huge", 1920, 1440),
                ]
            )
        )
        assert parsed.media is not None
        assert parsed.media.kind == "photo"
        assert parsed.media.file_id == "limit"
        assert parsed.media.width == 1280
        assert parsed.text == ""

    def test_photo_all_oversized_takes_smallest(self) -> None:
        parsed = _extract(
            _media_update(
                photo=[
                    _photo_size("mid", 2000, 1500),
                    _photo_size("min", 1600, 1200),
                ]
            )
        )
        assert parsed.media is not None
        assert parsed.media.file_id == "min"

    def test_animation_takes_priority_over_document(self) -> None:
        parsed = _extract(
            _media_update(
                animation={
                    "file_id": "anim-1",
                    "file_unique_id": "uniq-anim-1",
                    "width": 480,
                    "height": 270,
                    "duration": 3,
                    "mime_type": "video/mp4",
                    "thumbnail": _photo_size("anim-thumb", 320, 180),
                },
                document={
                    "file_id": "doc-1",
                    "file_unique_id": "uniq-doc-1",
                    "mime_type": "video/mp4",
                },
            )
        )
        assert parsed.media is not None
        assert parsed.media.kind == "animation"
        assert parsed.media.file_id == "anim-1"
        assert parsed.media.thumbnail_file_id == "anim-thumb"
        assert parsed.media.duration_seconds == 3

    def test_video_exposes_thumbnail_only(self) -> None:
        parsed = _extract(
            _media_update(
                video={
                    "file_id": "video-1",
                    "file_unique_id": "uniq-video-1",
                    "width": 1280,
                    "height": 720,
                    "duration": 12,
                    "thumbnail": _photo_size("video-thumb", 320, 180),
                }
            )
        )
        assert parsed.media is not None
        assert parsed.media.kind == "video"
        assert parsed.media.thumbnail_file_id == "video-thumb"
        assert parsed.media.duration_seconds == 12

    def test_video_note_uses_length(self) -> None:
        parsed = _extract(
            _media_update(
                video_note={
                    "file_id": "note-1",
                    "file_unique_id": "uniq-note-1",
                    "length": 240,
                    "duration": 5,
                    "thumbnail": _photo_size("note-thumb", 240, 240),
                }
            )
        )
        assert parsed.media is not None
        assert parsed.media.kind == "video_note"
        assert parsed.media.width == 240
        assert parsed.media.height == 240

    def test_static_sticker_uses_own_file(self) -> None:
        parsed = _extract(
            _media_update(
                sticker={
                    "file_id": "sticker-1",
                    "file_unique_id": "uniq-sticker-1",
                    "type": "regular",
                    "width": 512,
                    "height": 512,
                    "is_animated": False,
                    "is_video": False,
                    "emoji": "😂",
                    "set_name": "cats",
                }
            )
        )
        assert parsed.media is not None
        assert parsed.media.kind == "sticker"
        assert parsed.media.file_id == "sticker-1"
        assert parsed.media.thumbnail_file_id is None
        assert parsed.media.sticker_format == "static"
        assert parsed.media.emoji == "😂"
        assert parsed.media.sticker_set_name == "cats"

    def test_animated_sticker_uses_thumbnail(self) -> None:
        parsed = _extract(
            _media_update(
                sticker={
                    "file_id": "sticker-2",
                    "file_unique_id": "uniq-sticker-2",
                    "type": "regular",
                    "width": 512,
                    "height": 512,
                    "is_animated": True,
                    "is_video": False,
                    "thumbnail": _photo_size("sticker-thumb", 128, 128),
                }
            )
        )
        assert parsed.media is not None
        assert parsed.media.sticker_format == "animated"
        assert parsed.media.thumbnail_file_id == "sticker-thumb"

    def test_video_sticker_format(self) -> None:
        parsed = _extract(
            _media_update(
                sticker={
                    "file_id": "sticker-3",
                    "file_unique_id": "uniq-sticker-3",
                    "type": "regular",
                    "width": 512,
                    "height": 512,
                    "is_animated": False,
                    "is_video": True,
                    "thumbnail": _photo_size("sticker-thumb-3", 128, 128),
                }
            )
        )
        assert parsed.media is not None
        assert parsed.media.sticker_format == "video"

    def test_image_document_becomes_photo(self) -> None:
        parsed = _extract(
            _media_update(
                document={
                    "file_id": "doc-image",
                    "file_unique_id": "uniq-doc-image",
                    "mime_type": "image/png",
                    "file_name": "cat.png",
                    "file_size": 4096,
                    "thumbnail": _photo_size("doc-thumb", 128, 128),
                }
            )
        )
        assert parsed.media is not None
        assert parsed.media.kind == "photo"
        assert parsed.media.file_id == "doc-image"
        assert parsed.media.mime_type == "image/png"
        assert parsed.media.file_name == "cat.png"

    def test_non_image_document_is_ignored(self) -> None:
        parsed = _extract(
            _media_update(
                document={
                    "file_id": "doc-pdf",
                    "file_unique_id": "uniq-doc-pdf",
                    "mime_type": "application/pdf",
                }
            )
        )
        assert parsed.media is None

    def test_voice_is_metadata_only(self) -> None:
        parsed = _extract(
            _media_update(
                voice={
                    "file_id": "voice-1",
                    "file_unique_id": "uniq-voice-1",
                    "duration": 7,
                    "mime_type": "audio/ogg",
                }
            )
        )
        assert parsed.media is not None
        assert parsed.media.kind == "voice"
        assert parsed.media.duration_seconds == 7
        assert parsed.media.thumbnail_file_id is None

    def test_webhook_publishes_media_and_legacy_fields(
        self, client: TestClient, app: FastAPI
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_incoming = AsyncMock()
        app.state.publisher = mock_publisher

        with patch("app.chats.telegram.webhook.settings") as mock_settings:
            mock_settings.TELEGRAM_WEBHOOK_SECRET = "secret123"

            payload = _media_update(
                caption="смотри",
                photo=[
                    _photo_size("small", 90, 90),
                    _photo_size("big", 1024, 768),
                ],
            )
            response = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
                json=payload,
            )
            assert response.status_code == 202
            assert response.json()["status"] == "accepted"

        msg = mock_publisher.publish_incoming.await_args.args[0]
        assert msg.media is not None
        assert msg.media.kind == "photo"
        assert msg.media.file_id == "big"
        assert msg.media_type == "photo"
        assert msg.media_file_id == "big"
        assert msg.caption == "смотри"
        assert msg.text == "смотри"

    def test_webhook_accepts_sticker_without_text(
        self, client: TestClient, app: FastAPI
    ) -> None:
        mock_publisher = AsyncMock()
        mock_publisher.publish_incoming = AsyncMock()
        app.state.publisher = mock_publisher

        with patch("app.chats.telegram.webhook.settings") as mock_settings:
            mock_settings.TELEGRAM_WEBHOOK_SECRET = "secret123"

            payload = _media_update(
                sticker={
                    "file_id": "sticker-4",
                    "file_unique_id": "uniq-sticker-4",
                    "type": "regular",
                    "width": 512,
                    "height": 512,
                    "is_animated": False,
                    "is_video": False,
                }
            )
            response = client.post(
                "/webhook/telegram",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret123"},
                json=payload,
            )
            assert response.status_code == 202
            assert response.json()["status"] == "accepted"

        msg = mock_publisher.publish_incoming.await_args.args[0]
        assert msg.media is not None
        assert msg.media.kind == "sticker"
        assert msg.text == ""
