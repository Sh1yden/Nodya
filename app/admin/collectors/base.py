"""Typed result of a collector with freshness metadata."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Result:
    """Collected data or the reason why it is unavailable."""

    data: Any | None = None
    error: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    stale: bool = False

    @property
    def ok(self) -> bool:
        """Whether the collector produced data."""
        return self.data is not None
