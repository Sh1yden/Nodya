"""Integration tests for ``app.chats.telegram.webhook``."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chats.telegram.webhook import router


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
