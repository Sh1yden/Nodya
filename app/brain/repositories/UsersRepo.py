"""Users data access."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.models import Users

from .Base import BaseRepo


class UsersRepo(BaseRepo[Users]):
    """CRUD helpers for the users table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repo to a session.

        Args:
            session: Async session used for all queries.
        """
        super().__init__(session, Users)
