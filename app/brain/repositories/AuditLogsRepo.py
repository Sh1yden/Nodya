from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.models import AuditLogs

from .Base import BaseRepo


class AuditLogsRepo(BaseRepo[AuditLogs]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AuditLogs)
