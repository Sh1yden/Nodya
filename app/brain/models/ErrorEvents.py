"""ErrorEvents model: fingerprint-grouped runtime errors."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from .Base import Base


class ErrorEvents(Base):
    """A grouped error with occurrence counters and last snapshot."""

    __tablename__ = "error_events"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    logger: Mapped[str] = mapped_column(String(255), nullable=False)
    module: Mapped[str] = mapped_column(String(255), nullable=False)
    function: Mapped[str] = mapped_column(String(255), nullable=False)
    exception_type: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
