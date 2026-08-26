"""HardFacts data access."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    async def upsert_fact(
        self,
        user_id: UUID,
        category: str,
        key: str,
        value: dict,
        confidence: float,
    ) -> int:
        """Atomically insert or refresh one fact.

        Conflict on (user_id, category, key) updates value/confidence
        and bumps updated_at. Single statement — race-free against
        concurrent consolidation runs (unique index backed).

        Args:
            user_id: Fact owner.
            category: Grouping bucket (identity/preferences/...).
            key: Short fact name.
            value: JSON payload of the fact.
            confidence: Extraction confidence in [0, 1].

        Returns:
            fact_id of the inserted or updated row.
        """
        stmt = (
            pg_insert(HardFacts)
            .values(
                user_id=user_id,
                category=category,
                key=key,
                value=value,
                confidence=confidence,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "category", "key"],
                set_={
                    "value": value,
                    "confidence": confidence,
                    "updated_at": func.now(),
                },
            )
            .returning(HardFacts.fact_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

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
