"""Grouped runtime errors from the error_events table."""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brain.models import ErrorEvents
from app.brain.repositories import ErrorEventsRepo
from app.core.error_sink import redact_secrets

FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    """One fingerprint group with its last snapshot."""

    fingerprint: str
    level: str
    logger: str
    module: str
    function: str
    exception_type: str | None
    message: str
    traceback: str | None
    count: int
    first_seen: datetime
    last_seen: datetime


def _to_record(row: ErrorEvents) -> ErrorRecord:
    """Map an ORM row to a redacted ErrorRecord.

    Args:
        row: ErrorEvents model instance.

    Returns:
        Record safe to render in HTML.
    """
    return ErrorRecord(
        fingerprint=row.fingerprint,
        level=row.level,
        logger=row.logger,
        module=row.module,
        function=row.function,
        exception_type=row.exception_type,
        message=redact_secrets(row.message),
        traceback=(redact_secrets(row.traceback) if row.traceback else None),
        count=row.count,
        first_seen=row.first_seen,
        last_seen=row.last_seen,
    )


class ErrorsCollector:
    """Read error groups for the panel."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Configure the collector.

        Args:
            session_factory: Session factory for read-only queries.
        """
        self._session_factory = session_factory

    async def list_errors(
        self, limit: int = 50, level: str | None = None
    ) -> list[ErrorRecord]:
        """List the most recently seen error groups.

        Args:
            limit: Maximum rows (clamped to 1..100).
            level: Exact level filter such as ``ERROR``.

        Returns:
            Records ordered by last_seen descending.
        """
        bounded = max(1, min(limit, _MAX_LIMIT))
        async with self._session_factory() as session:
            rows = await ErrorEventsRepo(session).list_recent(
                limit=bounded, level=level
            )
        return [_to_record(row) for row in rows]

    async def get_error(self, fingerprint: str) -> ErrorRecord | None:
        """Load one error group by fingerprint.

        Args:
            fingerprint: 64-character hex digest.

        Returns:
            The record, or None when unknown or malformed.
        """
        if not FINGERPRINT_RE.match(fingerprint):
            return None
        async with self._session_factory() as session:
            row = await ErrorEventsRepo(session).get_by_fingerprint(
                fingerprint
            )
        return _to_record(row) if row is not None else None
