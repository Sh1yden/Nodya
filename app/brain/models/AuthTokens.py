from typing import Literal
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .Base import Base


class AuthTokens(Base):
    __tablename__ = "auth_tokens"
    __table_args__ = {"extend_existing": True}

    token_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id"), nullable=False
    )
    client_type: Mapped[Literal["browser", "cli"]] = mapped_column(
        String, nullable=False
    )
    # token_hash
    # created_at
    # last_used_at
    # revoked_at
