from typing import Any, Dict
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Uuid, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from .Base import Base


class Users(Base):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
        nullable=False,
    )
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discord_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    browser_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id"), nullable=False
    )
    cli_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id"), nullable=False
    )
    settings: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
