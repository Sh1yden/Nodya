"""ErrorEvents data access."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.models import ErrorEvents

from .Base import BaseRepo


@dataclass(frozen=True, slots=True)
class ErrorEventUpsert:
    """One error occurrence ready to be grouped in PostgreSQL."""

    fingerprint: str
    level: str
    logger: str
    module: str
    function: str
    message: str
    seen_at: datetime
    exception_type: str | None = None
    traceback: str | None = None
    count: int = 1


class ErrorEventsRepo(BaseRepo[ErrorEvents]):
    """CRUD helpers for the error_events table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repo to a session.

        Args:
            session: Async session used for all queries.
        """
        super().__init__(session, ErrorEvents)

    async def upsert_many(self, events: list[ErrorEventUpsert]) -> None:
        """Insert occurrences or bump counters of known fingerprints.

        Args:
            events: Occurrences collected from the error sink.
        """
        if not events:
            return

        stmt = insert(ErrorEvents).values(
            [
                {
                    "fingerprint": event.fingerprint,
                    "level": event.level,
                    "logger": event.logger,
                    "module": event.module,
                    "function": event.function,
                    "exception_type": event.exception_type,
                    "message": event.message,
                    "traceback": event.traceback,
                    "count": event.count,
                    "last_seen": event.seen_at,
                }
                for event in events
            ]
        )
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=[ErrorEvents.fingerprint],
            set_={
                "count": ErrorEvents.count + excluded.count,
                "last_seen": func.greatest(
                    ErrorEvents.last_seen, excluded.last_seen
                ),
                "level": excluded.level,
                "message": excluded.message,
                "traceback": excluded.traceback,
                "exception_type": excluded.exception_type,
            },
        )
        await self.session.execute(stmt)

    async def list_recent(
        self, limit: int = 50, level: str | None = None
    ) -> list[ErrorEvents]:
        """Return the most recently seen errors, newest first.

        Args:
            limit: Maximum number of rows.
            level: Optional exact level filter (e.g. ``ERROR``).

        Returns:
            Matching error rows ordered by ``last_seen`` descending.
        """
        stmt = select(ErrorEvents).order_by(ErrorEvents.last_seen.desc())
        if level is not None:
            stmt = stmt.where(ErrorEvents.level == level)
        result = await self.session.scalars(stmt.limit(limit))
        return list(result)

    async def get_by_fingerprint(self, fingerprint: str) -> ErrorEvents | None:
        """Fetch one grouped error by its fingerprint.

        Args:
            fingerprint: Stable hash identifying the error group.

        Returns:
            The row or None when absent.
        """
        return await self.get_by_field("fingerprint", fingerprint)

    async def delete_older_than(self, days: int) -> int:
        """Delete error groups not seen for the given number of days.

        Args:
            days: Retention window in days.

        Returns:
            Number of deleted rows.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await self.session.execute(
            delete(ErrorEvents).where(ErrorEvents.last_seen < cutoff)
        )
        return result.rowcount or 0
