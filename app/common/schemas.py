"""Transport DTOs exchanged via RabbitMQ.

Pydantic models only: SQLAlchemy objects never cross process
boundaries.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

Channel = Literal["telegram", "discord", "browser", "cli"]
MediaKind = Literal[
    "photo",
    "video",
    "voice",
    "audio",
    "animation",
    "sticker",
    "video_note",
]
StickerFormat = Literal["static", "animated", "video"]


class MediaDescriptor(BaseModel):
    """Channel-agnostic description of an attached media item.

    Binaries never cross the queue: the descriptor carries channel
    file ids and metadata, and the recognition module downloads only
    the frame it needs (the image itself or a thumbnail).
    """

    kind: MediaKind
    file_id: str
    thumbnail_file_id: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    file_size: int | None = None
    emoji: str | None = None
    sticker_format: StickerFormat | None = None
    sticker_set_name: str | None = None
    file_name: str | None = None


class IncomingMessage(BaseModel):
    """A user message accepted from a channel.

    ``user_external_id`` identifies the sender, ``chat_external_id``
    is where the reply must be delivered (they differ in group
    chats). Media carries ``media`` (plus legacy ``media_type`` /
    ``media_file_id`` for old queued payloads); ``text`` is the
    caption or plain text.
    """

    user_external_id: str
    channel: Channel
    text: str
    received_at: datetime
    chat_external_id: str | None = None
    sender_name: str | None = None
    sender_username: str | None = None
    media: MediaDescriptor | None = None
    media_type: MediaKind | None = None
    media_file_id: str | None = None
    caption: str | None = None


class OutgoingMessage(BaseModel):
    """A reply or proactive message of Nodya for delivery.

    ``chat_external_id`` pins the destination chat of the incoming
    batch (groups); None means the channel default (direct chat).
    """

    user_id: UUID
    channel: Channel
    text: str
    delay_until: datetime | None = None
    chat_external_id: str | None = None


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


class TypingEvent(BaseModel):
    """Channel typing indicator event (ADR-17).

    Published by Worker while generating, consumed by channel
    senders to show ``typing`` status. Separate routing key from
    outgoing to keep generation and delivery decoupled.
    """

    user_id: UUID
    channel: Channel
    action: Literal["start", "stop"]
    chat_id: int | None = None
