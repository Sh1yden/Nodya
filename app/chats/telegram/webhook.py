"""Telegram webhook: проверка секрета и публикация в RabbitMQ.

Gateway намеренно не использует Dispatcher/хендлеры aiogram — только
парсинг Update и публикацию IncomingMessage (принцип «только приём»).
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
    """Принять update от Telegram и опубликовать в очередь incoming."""
    _check_secret(request)
    publisher = request.app.state.publisher
    raw_update = await request.json()
    try:
        update = Update.model_validate(raw_update)
    except ValueError:
        logger.warning("Невалидный payload вебхука Telegram.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid update",
        ) from None

    chat_id, text = _extract_text(update)
    if chat_id is None:
        return {"status": "ignored"}
    if not text:
        logger.debug("Non-text update проигнорирован: chat_id=%s", chat_id)
        return {"status": "ignored"}

    message = IncomingMessage(
        user_external_id=str(chat_id),
        channel="telegram",
        text=text,
        received_at=datetime.now(UTC),
    )
    await publisher.publish_incoming(message)
    logger.info("Update принят: chat_id=%s, len=%d.", chat_id, len(text))
    return {"status": "accepted"}


def _check_secret(request: Request) -> None:
    """Сверить заголовок секрета с TELEGRAM_WEBHOOK_SECRET."""
    received = request.headers.get(_SECRET_HEADER, "")
    if not settings.TELEGRAM_WEBHOOK_SECRET or not secrets.compare_digest(
        received, settings.TELEGRAM_WEBHOOK_SECRET
    ):
        logger.warning("Вебхук с неверным секретом отклонён.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="forbidden",
        )


def _extract_text(update: Update) -> tuple[int | None, str]:
    """Достать chat_id и текст из простого текстового сообщения."""
    if update.message is None or update.message.text is None:
        return None, ""
    return (
        update.message.chat.id,
        update.message.text.strip(),
    )
