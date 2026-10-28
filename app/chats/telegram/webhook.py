"""Telegram webhook: secret validation + publishing into RabbitMQ.

The Gateway deliberately avoids aiogram Dispatcher/handlers — only
Update parsing and IncomingMessage publishing (receive-only principle).
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram.types import Message, PhotoSize, Update
from fastapi import APIRouter, HTTPException, Request, status

from app.common import IncomingMessage, MediaDescriptor
from app.core import get_logger, settings

logger = get_logger(__name__)

router = APIRouter()

_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

_MAX_PHOTO_WIDTH = 1280


@dataclass(slots=True)
class _ParsedMessage:
    """Fields of one Telegram message relevant to the pipeline."""

    chat_id: int | None
    sender_id: int | None
    text: str
    media: MediaDescriptor | None
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
    # Allow any media with empty text (stickers have no caption).
    if not parsed.text and parsed.media is None:
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
        media=parsed.media,
        media_type=parsed.media.kind if parsed.media else None,
        media_file_id=parsed.media.file_id if parsed.media else None,
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
    empty = _ParsedMessage(None, None, "", None, None, None, None)
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

    if text:
        return _ParsedMessage(
            chat_id,
            sender_id,
            text,
            None,
            caption,
            sender_name,
            sender_username,
        )

    media = _descriptor_from_message(msg)
    if media is not None:
        return _ParsedMessage(
            chat_id,
            sender_id,
            caption or "",
            media,
            caption,
            sender_name,
            sender_username,
        )

    if caption is not None:
        return _ParsedMessage(
            chat_id,
            sender_id,
            caption,
            None,
            caption,
            sender_name,
            sender_username,
        )

    return empty


def _descriptor_from_message(msg: Message) -> MediaDescriptor | None:
    """Normalize Telegram media into a channel-agnostic descriptor.

    Animation is checked before document because Telegram also
    duplicates GIF messages into ``document``. For video-like kinds
    only the thumbnail is exposed to vision; the main file is never
    fetched.

    Args:
        msg: Telegram message with at most one media attachment.

    Returns:
        Media descriptor, or None for text/unsupported attachments.
    """
    if msg.animation is not None:
        item = msg.animation
        return MediaDescriptor(
            kind="animation",
            file_id=item.file_id,
            thumbnail_file_id=_thumb_id(item.thumbnail),
            mime_type=item.mime_type,
            width=item.width,
            height=item.height,
            duration_seconds=item.duration,
            file_size=item.file_size,
            file_name=item.file_name,
        )

    if msg.photo:
        photo = _pick_photo(msg.photo)
        return MediaDescriptor(
            kind="photo",
            file_id=photo.file_id,
            width=photo.width,
            height=photo.height,
            file_size=photo.file_size,
        )

    if msg.video is not None:
        item = msg.video
        return MediaDescriptor(
            kind="video",
            file_id=item.file_id,
            thumbnail_file_id=_thumb_id(item.thumbnail),
            mime_type=item.mime_type,
            width=item.width,
            height=item.height,
            duration_seconds=item.duration,
            file_size=item.file_size,
            file_name=item.file_name,
        )

    if msg.video_note is not None:
        item = msg.video_note
        return MediaDescriptor(
            kind="video_note",
            file_id=item.file_id,
            thumbnail_file_id=_thumb_id(item.thumbnail),
            width=item.length,
            height=item.length,
            duration_seconds=item.duration,
            file_size=item.file_size,
        )

    if msg.sticker is not None:
        item = msg.sticker
        if item.is_animated:
            sticker_format = "animated"
        elif item.is_video:
            sticker_format = "video"
        else:
            sticker_format = "static"
        return MediaDescriptor(
            kind="sticker",
            file_id=item.file_id,
            thumbnail_file_id=_thumb_id(item.thumbnail),
            width=item.width,
            height=item.height,
            file_size=item.file_size,
            emoji=item.emoji,
            sticker_format=sticker_format,
            sticker_set_name=item.set_name,
        )

    if msg.voice is not None:
        item = msg.voice
        return MediaDescriptor(
            kind="voice",
            file_id=item.file_id,
            mime_type=item.mime_type,
            duration_seconds=item.duration,
            file_size=item.file_size,
        )

    if msg.audio is not None:
        item = msg.audio
        return MediaDescriptor(
            kind="audio",
            file_id=item.file_id,
            thumbnail_file_id=_thumb_id(item.thumbnail),
            mime_type=item.mime_type,
            duration_seconds=item.duration,
            file_size=item.file_size,
            file_name=item.file_name,
        )

    if msg.document is not None:
        item = msg.document
        if (item.mime_type or "").startswith("image/"):
            return MediaDescriptor(
                kind="photo",
                file_id=item.file_id,
                thumbnail_file_id=_thumb_id(item.thumbnail),
                mime_type=item.mime_type,
                file_size=item.file_size,
                file_name=item.file_name,
            )

    return None


def _pick_photo(
    photos: list[PhotoSize], max_width: int = _MAX_PHOTO_WIDTH
) -> PhotoSize:
    """Pick the largest photo that fits the vision budget.

    Args:
        photos: Telegram photo sizes, smallest to largest.
        max_width: Preferred upper bound on width.

    Returns:
        Largest fitting size, or the smallest one when all exceed
        the bound (keeps the payload small).
    """
    fitting = [p for p in photos if p.width <= max_width]
    if fitting:
        return max(fitting, key=lambda p: p.width * p.height)
    return min(photos, key=lambda p: p.width * p.height)


def _thumb_id(thumbnail: PhotoSize | None) -> str | None:
    """Return a thumbnail file id when Telegram provided one."""
    return thumbnail.file_id if thumbnail is not None else None
