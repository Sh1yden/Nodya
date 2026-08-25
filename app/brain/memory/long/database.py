"""Async engine, session factory and FastAPI dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core import settings

engine = create_async_engine(
    settings.postgres_url,
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Provide a request-scoped database session.

    Yields:
        AsyncSession bound to the application engine.
    """
    async with AsyncSessionLocal() as session:
        yield session
