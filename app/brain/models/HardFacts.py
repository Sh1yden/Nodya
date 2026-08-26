"""HardFacts model: long-term structured facts per user."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .Base import Base


class HardFacts(Base):
    """A durable fact extracted from dialogues (consolidation)."""

    __tablename__ = "hard_facts"
    __table_args__ = (
        # Enables atomic ON CONFLICT upsert; one fact per identity.
        UniqueConstraint(
            "user_id", "category", "key", name="uq_hard_facts_identity"
        ),
    )

    fact_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
