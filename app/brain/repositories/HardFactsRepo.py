"""HardFacts data access."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.models import HardFacts

from .Base import BaseRepo


class HardFactsRepo(BaseRepo[HardFacts]):
    """Queries for user facts used by context assembly."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repo to a session.

        Args:
            session: Async session used for all queries.
        """
        super().__init__(session, HardFacts)

    async def get_facts_by_uuid(self, user_id: UUID) -> list[HardFacts]:
        """Fetch every fact belonging to a user.

        Args:
            user_id: Internal user UUID.

        Returns:
            All matching facts (unordered).
        """
        stmt = select(self.model).where(self.model.user_id == user_id)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def search_last_updated(
        self, user_id: UUID, limit: int = 20
    ) -> list[HardFacts]:
        """Fetch the most recently updated facts of a user.

        Args:
            user_id: Internal user UUID.
            limit: Max number of rows.

        Returns:
            Facts ordered by updated_at descending.
        """
        stmt = (
            select(self.model)
            .where(self.model.user_id == user_id)
            .order_by(self.model.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.scalars(stmt)
        return list(result.all())
