"""Transport DTOs exchanged via RabbitMQ.

Pydantic models only: SQLAlchemy objects never cross process
boundaries.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Channel = Literal["telegram", "discord", "browser", "cli"]


class IncomingMessage(BaseModel):
    """A user message accepted from a channel."""

    user_external_id: str
    channel: Channel
    text: str
    received_at: datetime


class OutgoingMessage(BaseModel):
    """A reply or proactive message of Nodya for delivery."""

    user_id: UUID
    channel: Channel
    text: str
    delay_until: datetime | None = None


class ScheduledEnvelope(BaseModel):
    """An entry of the ``nodya:scheduled`` ZSet (ADR-13/15).

    Single envelope for two cases:
    - ``kind="incoming"`` — an incoming batch postponed because the
      user lock was busy (``retry_count`` grows on repeats);
    - ``kind="outgoing"`` — an outgoing message whose ``delay_until``
      lies in the future.
    """

    kind: Literal["incoming", "outgoing"]
    incoming: list[IncomingMessage] | None = None
    outgoing: OutgoingMessage | None = None
    retry_count: int = 0
