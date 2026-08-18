from datetime import datetime
from typing import Any, Dict, Literal
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, String, Uuid, func
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
    telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    discord_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True
    )
    username: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    passwd_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[Literal["owner", "user"]] = mapped_column(String, nullable=False)
    settings: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
