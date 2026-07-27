from sqlalchemy.ext.asyncio import AsyncSession

from .Base import BaseRepo
from app.brain.models import AuditLogs


class AuditLogsRepo(BaseRepo[AuditLogs]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AuditLogs)
