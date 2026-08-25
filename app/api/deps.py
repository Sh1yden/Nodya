"""FastAPI authentication dependencies."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.memory.long import get_db
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
    """Resolve a user from a Bearer token.

    Hashes the raw token with SHA-256, looks up a non-revoked row in
    auth_tokens and refreshes last_used_at.

    Args:
        authorization: Raw Authorization header value.
        session: Request-scoped database session.

    Returns:
        The authenticated Users object.

    Raises:
        HTTPException 401: Missing/malformed header, unknown token,
            revoked token or dangling user reference.
    """
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
