from typing import Any

from sqlalchemy import inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepo[ModelType]:
    def __init__(self, session: AsyncSession, model: type[ModelType]) -> None:
        self.session = session
        self.model = model

    async def get_by_id(self, id: Any) -> ModelType | None:
        """Ultrafast method for search by Primary Key."""
        return await self.session.get(self.model, id)

    async def get_by_field(
        self, field_name: str, value: Any
    ) -> ModelType | None:
        """Universal search for another fields."""

        if not hasattr(self.model, field_name):
            raise ValueError(
                f"Columns {field_name} not created in {self.model.__name__}."
            )

        stmt = select(self.model).where(
            getattr(self.model, field_name) == value
        )
        result = await self.session.execute(stmt)

        return result.scalar_one_or_none()

    def add(self, obj: ModelType) -> None:
        self.session.add(obj)

    async def delete(self, obj: ModelType) -> None:
        await self.session.delete(obj)

    async def update(self, id: Any, **kwargs) -> None:
        """
        Update request into database by ID.
        Kwargs - dict of fields to update.
        (This is func for only massive update).
        """
        pk = inspect(self.model).primary_key[0]  # type: ignore

        stmt = update(self.model).where(pk == id).values(**kwargs)

        await self.session.execute(stmt)
