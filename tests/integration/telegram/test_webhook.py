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
) -> dict:
    """Build a realistic Telegram update payload."""
    return {
        "update_id": 1,
        "message": {
            "message_id": message_id,
            "from": {
                "id": chat_id,
                "is_bot": False,
                "first_name": "Test",
                "username": "testuser",
            },
            "chat": {
                "id": chat_id,
                "type": "private",
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
