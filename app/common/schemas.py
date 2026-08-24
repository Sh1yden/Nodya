"""Обменные схемы (DTO) для транспорта через RabbitMQ.

Только Pydantic-модели: SQLAlchemy-объекты между процессами не ходят.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Channel = Literal["telegram", "discord", "browser", "cli"]


class IncomingMessage(BaseModel):
    """Сообщение пользователя, принятое каналом."""

    user_external_id: str
    channel: Channel
    text: str
    received_at: datetime


class OutgoingMessage(BaseModel):
    """Ответ/проактивное сообщение Ноди для доставки в канал."""

    user_id: UUID
    channel: Channel
    text: str
    delay_until: datetime | None = None


class ScheduledEnvelope(BaseModel):
    """Элемент ZSet ``nodya:scheduled`` (ADR-13/15).

    Единый конверт для двух случаев:
    - ``kind="incoming"`` — пачка входящих, отложенная из-за занятого
      лока (``retry_count`` растёт при повторных неудачах);
    - ``kind="outgoing"`` — исходящее с ``delay_until`` в будущем.
    """

    kind: Literal["incoming", "outgoing"]
    incoming: list[IncomingMessage] | None = None
    outgoing: OutgoingMessage | None = None
    retry_count: int = 0
