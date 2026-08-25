"""Users model: accounts across all channels."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .Base import Base


class Users(Base):
    """A Nodya user reachable via telegram/discord/browser/cli."""

    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        nullable=False,
    )
    telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    discord_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    username: Mapped[str] = mapped_column(
        String(20), nullable=False, unique=True
    )
    passwd_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[Literal["owner", "user"]] = mapped_column(
        String, nullable=False
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    auth_tokens: Mapped[list["AuthTokens"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
