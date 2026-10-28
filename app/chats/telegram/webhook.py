"""Telegram webhook: secret validation + publishing into RabbitMQ.

The Gateway deliberately avoids aiogram Dispatcher/handlers — only
Update parsing and IncomingMessage publishing (receive-only principle).
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request, status

from app.common import IncomingMessage
from app.core import get_logger, settings

logger = get_logger(__name__)

router = APIRouter()

_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

MediaType = Literal["photo", "video", "voice", "audio"]


@dataclass(slots=True)
class _ParsedMessage:
    """Fields of one Telegram message relevant to the pipeline."""

    chat_id: int | None
    sender_id: int | None
    text: str
    media_type: MediaType | None
    file_id: str | None
    caption: str | None
    sender_name: str | None
    sender_username: str | None


@router.post(
    "/webhook/telegram",
    status_code=status.HTTP_202_ACCEPTED,
)
async def telegram_webhook(request: Request) -> dict[str, str]:
    """Accept a Telegram update and publish it to the incoming queue.

    The sender (``from_user``) is the identity; ``chat.id`` is only
    the delivery destination, so group participants never share an
    account.

    Args:
        request: Raw request carrying the Telegram update JSON.

    Returns:
        Short status dict: accepted / ignored.

    Raises:
        HTTPException 403: Secret header missing or mismatched.
        HTTPException 400: Payload is not a valid Telegram update.
    """
    _check_secret(request)
    publisher = request.app.state.publisher
    raw_update = await request.json()
    try:
        update = Update.model_validate(raw_update)
    except ValueError:
        logger.warning("Rejected malformed Telegram webhook payload.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid update",
        ) from None

    parsed = _extract_message(update)
    if parsed.chat_id is None or parsed.sender_id is None:
        return {"status": "ignored"}
    # Allow photo/video/voice with empty text but with media
    if not parsed.text and parsed.media_type is None:
        logger.debug(f"Ignored non-text update: chat_id={parsed.chat_id}")
        return {"status": "ignored"}

    message = IncomingMessage(
        user_external_id=str(parsed.sender_id),
        chat_external_id=str(parsed.chat_id),
        channel="telegram",
        text=parsed.text,
        received_at=datetime.now(UTC),
        sender_name=parsed.sender_name,
        sender_username=parsed.sender_username,
        media_type=parsed.media_type,
        media_file_id=parsed.file_id,
        caption=parsed.caption,
    )
    await publisher.publish_incoming(message)
    logger.debug(
        f"Update accepted: sender_id={parsed.sender_id}, "
        f"chat_id={parsed.chat_id}, len={len(parsed.text)}"
    )
    return {"status": "accepted"}


def _check_secret(request: Request) -> None:
    """Compare the secret header with TELEGRAM_WEBHOOK_SECRET.

    Args:
        request: Incoming webhook request.

    Raises:
        HTTPException 403: Header absent or mismatched.
    """
    received = request.headers.get(_SECRET_HEADER, "")
    if not settings.TELEGRAM_WEBHOOK_SECRET or not secrets.compare_digest(
        received, settings.TELEGRAM_WEBHOOK_SECRET
    ):
        logger.warning("Webhook rejected: bad secret header.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        )


def _extract_message(update: Update) -> _ParsedMessage:
    """Pull sender, chat, text and optional media from an update.

    Returns:
        Parsed message; ``chat_id``/``sender_id`` are None when the
        update carries no message. ``sender_id`` falls back to the
        chat id for channel posts without ``from_user``.
    """
    empty = _ParsedMessage(None, None, "", None, None, None, None, None)
    if update.message is None:
        return empty

    msg = update.message
    chat_id = msg.chat.id
    sender = msg.from_user
    sender_id = sender.id if sender is not None else chat_id
    sender_name = None
    sender_username = None
    if sender is not None:
        parts = (sender.first_name, sender.last_name)
        sender_name = " ".join(p for p in parts if p).strip() or None
        sender_username = sender.username

    text = (msg.text or "").strip()
    caption = (msg.caption or "").strip() or None

    def _make(
        media_type: MediaType | None = None,
        file_id: str | None = None,
        body: str | None = None,
    ) -> _ParsedMessage:
        return _ParsedMessage(
            chat_id=chat_id,
            sender_id=sender_id,
            text=body if body is not None else text,
            media_type=media_type,
            file_id=file_id,
            caption=caption,
            sender_name=sender_name,
            sender_username=sender_username,
        )

    if text:
        return _make()

    if msg.photo:
        # photo is list[PhotoSize], last is biggest
        return _make("photo", msg.photo[-1].file_id, caption or "")

    if msg.video and msg.video.file_id:
        return _make("video", msg.video.file_id, caption or "")

    if msg.voice and msg.voice.file_id:
        return _make("voice", msg.voice.file_id, caption or "")

    if msg.audio and msg.audio.file_id:
        return _make("audio", msg.audio.file_id, caption or "")

    if msg.document and msg.document.file_id:
        # Fallback: treat document as photo if mime image
        mime = msg.document.mime_type or ""
        if mime.startswith("image/"):
            return _make("photo", msg.document.file_id, caption or "")

    if caption is not None:
        return _make(body=caption)

    return empty
