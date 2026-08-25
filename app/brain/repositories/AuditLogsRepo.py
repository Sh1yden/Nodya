"""AuditLogs data access."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.models import AuditLogs

from .Base import BaseRepo


class AuditLogsRepo(BaseRepo[AuditLogs]):
    """CRUD helpers for the audit_logs table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repo to a session.

        Args:
            session: Async session used for all queries.
        """
        super().__init__(session, AuditLogs)
