"""FastAPI-зависимости аутентификации."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.memory.long.database import get_db
from app.brain.models import AuthTokens, Users
from app.brain.repositories.security import hash_token

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="unauthorized",
)

_SessionDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    authorization: Annotated[str, Header()],
    session: _SessionDep,
) -> Users:
    """Bearer-токен -> sha256 -> AuthTokens (не отозван) -> Users."""
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        raise _UNAUTHORIZED
    result = await session.execute(
        select(AuthTokens).where(
            AuthTokens.token_hash == hash_token(raw_token),
            AuthTokens.revoked_at.is_(None),
        )
    )
    token_row = result.scalar_one_or_none()
    if token_row is None:
        raise _UNAUTHORIZED
    user = await session.get(Users, token_row.user_id)
    if user is None:
        raise _UNAUTHORIZED
    token_row.last_used_at = datetime.now(UTC)
    await session.commit()
    return user
