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

    chat_id, text = _extract_text(update)
    if chat_id is None:
        return {"status": "ignored"}
    if not text:
        logger.debug(f"Ignored non-text update: chat_id={chat_id}")
        return {"status": "ignored"}

    message = IncomingMessage(
        user_external_id=str(chat_id),
        channel="telegram",
        text=text,
        received_at=datetime.now(UTC),
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
