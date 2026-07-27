from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from .Base import Base


class AuditLogs(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"extend_existing": True}

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.user_id"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    arguments: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
