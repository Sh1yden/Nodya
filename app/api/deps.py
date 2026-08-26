"""FastAPI authentication dependencies."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.memory.long import get_db
from app.brain.models import AuthTokens, Users
from app.brain.repositories.security import hash_token

# auto_error=False: absent header must produce OUR generic 401,
# not FastAPI's default 403 "Not authenticated".
_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="unauthorized",
)

_SessionDep = Annotated[AsyncSession, Depends(get_db)]
_CredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None, Depends(_bearer)
]


async def get_current_user(
    credentials: _CredentialsDep,
    session: _SessionDep,
) -> Users:
    """Resolve a user from a Bearer token.

    Hashes the raw token with SHA-256, looks up a non-revoked row in
    auth_tokens and refreshes last_used_at. Declared via HTTPBearer,
    so Swagger exposes the global Authorize dialog for it.

    Args:
        credentials: Parsed Authorization header (scheme + token).
        session: Request-scoped database session.

    Returns:
        The authenticated Users object.

    Raises:
        HTTPException 401: Missing/malformed header, unknown token,
            revoked token or dangling user reference.
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED
    result = await session.execute(
        select(AuthTokens).where(
            AuthTokens.token_hash == hash_token(credentials.credentials),
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
