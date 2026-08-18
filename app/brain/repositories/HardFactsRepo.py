from typing import Any, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .Base import BaseRepo
from app.brain.models import HardFacts


class HardFactsRepo(BaseRepo[HardFacts]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, HardFacts)

    async def get_facts_by_uuid(self, user_id: Any) -> None | List:
        """Get all facts connect to user. By user id."""

        stmt = select(self.model).where(self.model.user_id == user_id)
        result = await self.session.scalars(stmt)

        return list(result.all())

    async def search_last_updated(
        self, user_id: Any, limit: int = 20
    ) -> list[HardFacts]:
        """"""
        stmt = (
            select(self.model)
            .where(self.model.user_id == user_id)
            .order_by(self.model.updated_at.desc())
            .limit(limit)
        )
        result = self.session.scalars(stmt)

        return list(result.all())
