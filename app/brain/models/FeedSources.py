"""FeedSources model: RSS/Telegram feeds of the background parser."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .Base import Base


class FeedSources(Base):
    """A feed polled by the background parser (stage 7.0)."""

    __tablename__ = "feed_sources"
    __table_args__ = (
        CheckConstraint("kind IN ('rss', 'telegram')", name="kind_valid"),
    )

    source_id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4, nullable=False
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    kind: Mapped[Literal["rss", "telegram"]] = mapped_column(
        String(16),
        nullable=False,
        default="rss",
        server_default="rss",
    )
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
