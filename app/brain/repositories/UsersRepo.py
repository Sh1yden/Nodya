from sqlalchemy.ext.asyncio import AsyncSession

from .Base import BaseRepo
from app.brain.models import Users


class UsersRepo(BaseRepo[Users]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Users)
