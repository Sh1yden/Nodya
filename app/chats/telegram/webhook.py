"""Telegram webhook: secret validation + publishing into RabbitMQ.

The Gateway deliberately avoids aiogram Dispatcher/handlers — only
Update parsing and IncomingMessage publishing (receive-only principle).
"""

import secrets
from datetime import UTC, datetime

from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request, status

from app.common import IncomingMessage
from app.core import get_logger, settings

logger = get_logger(__name__)

router = APIRouter()

_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@router.post(
    "/webhook/telegram",
    status_code=status.HTTP_202_ACCEPTED,
)
async def telegram_webhook(request: Request) -> dict[str, str]:
    """Accept a Telegram update and publish it to the incoming queue.

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

    chat_id, text, media_type, file_id, caption = _extract_message(update)
    if chat_id is None:
        return {"status": "ignored"}
    # Allow photo/video/voice with empty text but with media
    if not text and media_type is None:
        logger.debug(f"Ignored non-text update: chat_id={chat_id}")
        return {"status": "ignored"}

    # For media, text is caption or empty; caption kept separately
    message = IncomingMessage(
        user_external_id=str(chat_id),
        channel="telegram",
        text=text,
        received_at=datetime.now(UTC),
        media_type=media_type,
        media_file_id=file_id,
        caption=caption,
    )
    await publisher.publish_incoming(message)
    logger.debug(f"Update accepted: chat_id={chat_id}, len={len(text)}")
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


def _extract_text(update: Update) -> tuple[int | None, str]:
    """Pull chat id and text from a plain text message.

    Args:
        update: Parsed Telegram update.

    Returns:
        Tuple of (chat_id, stripped text); (None, "") when the update
        carries no text message.
    """
    if update.message is None or update.message.text is None:
        return None, ""
    return (
        update.message.chat.id,
        update.message.text.strip(),
    )


def _extract_message(
    update: Update,
) -> tuple[int | None, str, str | None, str | None, str | None]:
    """Pull chat id, text and optional media from a message.

    Returns:
        (chat_id, text, media_type, file_id, caption).
        media_type is photo/video/voice/audio or None.
    """
    if update.message is None:
        return None, "", None, None, None

    msg = update.message
    chat_id = msg.chat.id

    # Text has priority but caption is fallback for media
    text = (msg.text or "").strip()
    caption = (msg.caption or "").strip() or None

    # If text present, treat as text; but keep caption for completeness
    if text:
        return chat_id, text, None, None, caption

    # Photo: largest size last
    if msg.photo:
        # photo is list[PhotoSize], last is biggest
        file_id = msg.photo[-1].file_id
        cap = caption or ""
        return chat_id, cap, "photo", file_id, caption

    if msg.video and msg.video.file_id:
        return chat_id, caption or "", "video", msg.video.file_id, caption

    if msg.voice and msg.voice.file_id:
        return chat_id, caption or "", "voice", msg.voice.file_id, caption

    if msg.audio and msg.audio.file_id:
        return chat_id, caption or "", "audio", msg.audio.file_id, caption

    if msg.document and msg.document.file_id:
        # Fallback: treat document as photo if mime image
        mime = msg.document.mime_type or ""
        if mime.startswith("image/"):
            return (
                chat_id,
                caption or "",
                "photo",
                msg.document.file_id,
                caption,
            )

    # No media, no text -> ignored
    if not text and caption is None:
        # check if there was any text at all
        return None, "", None, None, None

    return chat_id, text or (caption or ""), None, None, caption
