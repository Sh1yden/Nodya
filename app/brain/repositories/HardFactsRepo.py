from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.models import HardFacts

from .Base import BaseRepo


class HardFactsRepo(BaseRepo[HardFacts]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, HardFacts)

    async def get_facts_by_uuid(self, user_id: UUID) -> list[HardFacts]:
        """Все факты пользователя по его user_id."""
        stmt = select(self.model).where(self.model.user_id == user_id)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def search_last_updated(
        self, user_id: UUID, limit: int = 20
    ) -> list[HardFacts]:
        """Самые свежие факты пользователя."""
        stmt = (
            select(self.model)
            .where(self.model.user_id == user_id)
            .order_by(self.model.updated_at.desc())
            .limit(limit)
        )
        result = await self.session.scalars(stmt)
        return list(result.all())
