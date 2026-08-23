from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.models import Users

from .Base import BaseRepo


class UsersRepo(BaseRepo[Users]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Users)
