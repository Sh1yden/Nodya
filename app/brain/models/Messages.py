from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .Base import Base


class Messages(Base):
    """Сырой архив переписки (ADR-14). Consolidation НЕ удаляет."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_user_created", "user_id", "created_at"),
    )

    message_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[Literal["incoming", "outgoing"]] = mapped_column(
        String(8), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
