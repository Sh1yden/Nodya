"""FeedSources data access."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.models import FeedSources

from .Base import BaseRepo


class FeedSourcesRepo(BaseRepo[FeedSources]):
    """Queries for the background parser feed list."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repo to a session.

        Args:
            session: Async session used for all queries.
        """
        super().__init__(session, FeedSources)

    async def list_active(self) -> list[FeedSources]:
        """Fetch every active feed source.

        Returns:
            Active sources ordered by creation time.
        """
        stmt = (
            select(self.model)
            .where(self.model.is_active.is_(True))
            .order_by(self.model.created_at)
        )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def list_all(self) -> list[FeedSources]:
        """Fetch every source, active or not.

        Returns:
            Sources ordered by creation time.
        """
        stmt = select(self.model).order_by(self.model.created_at)
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_by_url(self, url: str) -> FeedSources | None:
        """Find a source by its canonical URL.

        Args:
            url: Canonical fetch URL.

        Returns:
            The matching row, or None.
        """
        stmt = select(self.model).where(self.model.url == url)
        return await self.session.scalar(stmt)

    async def count_active(self) -> int:
        """Count currently active sources.

        Returns:
            Number of rows with is_active set.
        """
        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.is_active.is_(True))
        )
        return int(await self.session.scalar(stmt) or 0)

    async def touch(self, source_id: UUID) -> None:
        """Record a successful poll of a source.

        Args:
            source_id: Source that was just scanned.
        """
        await self.session.execute(
            sa_update(self.model)
            .where(self.model.source_id == source_id)
            .values(last_checked_at=datetime.now(UTC))
        )
