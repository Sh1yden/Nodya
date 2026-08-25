"""Generic repository base with common CRUD helpers."""

from typing import Any

from sqlalchemy import inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepo[ModelType]:
    """Data-access helper bound to one model and session.

    The repository never commits: transaction control belongs to the
    calling service/worker layer (unit of work lives there).
    """

    def __init__(self, session: AsyncSession, model: type[ModelType]) -> None:
        """Bind the repo to a session and a model class.

        Args:
            session: Async session used for all queries.
            model: Mapped model class this repo serves.
        """
        self.session = session
        self.model = model

    async def get_by_id(self, id: Any) -> ModelType | None:
        """Fetch a single row by primary key.

        Args:
            id: Primary key value.

        Returns:
            Model instance or None when absent.
        """
        return await self.session.get(self.model, id)

    async def get_by_field(
        self, field_name: str, value: Any
    ) -> ModelType | None:
        """Fetch a single row by an arbitrary column.

        Args:
            field_name: Column attribute name on the model.
            value: Value to match exactly.

        Returns:
            First matching instance or None.

        Raises:
            ValueError: The model has no such attribute.
        """
        if not hasattr(self.model, field_name):
            raise ValueError(
                f"Column {field_name} does not exist on {self.model.__name__}."
            )

        stmt = select(self.model).where(
            getattr(self.model, field_name) == value
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def add(self, obj: ModelType) -> None:
        """Stage a new object for insertion (flushed on commit).

        Args:
            obj: Unsaved model instance.
        """
        self.session.add(obj)

    async def delete(self, obj: ModelType) -> None:
        """Mark an object for deletion (executed on commit).

        Args:
            obj: Persistent instance to remove.
        """
        await self.session.delete(obj)

    async def update(self, id: Any, **kwargs) -> None:
        """Bulk-update columns of a row by primary key.

        Args:
            id: Primary key value of the target row.
            **kwargs: Column names and their new values.
        """
        pk = inspect(self.model).primary_key[0]  # type: ignore
        stmt = update(self.model).where(pk == id).values(**kwargs)
        await self.session.execute(stmt)
