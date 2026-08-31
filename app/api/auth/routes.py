"""Authentication: register / login / logout / account deletion.

Passwords are hashed with argon2id, opaque tokens with SHA-256
(ADR-3). Owner is created only via bootstrap CLI (ADR-16), public
registration always creates ``role=user``.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.brain.memory.long import get_db
from app.brain.memory.short import LINK_TTL_SECONDS, RedisClient
from app.brain.models import AuthTokens, Users
from app.brain.repositories import UsersRepo
from app.brain.repositories.security import (
    generate_token,
    hash_password,
    hash_token,
    verify_password,
    verify_password_dummy,
)
from app.core import get_logger

from .schemas import (
    LinkCodeResponse,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_SessionDep = Annotated[AsyncSession, Depends(get_db)]
_CurrentUserDep = Annotated[Users, Depends(get_current_user)]


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
)
async def register(
    payload: RegisterRequest,
    session: _SessionDep,
) -> RegisterResponse:
    """Create a user and issue the first access token.

    Args:
        payload: Registration data (username, password, client type).
        session: Request-scoped database session.

    Returns:
        New user id together with the one-time plaintext token.

    Raises:
        HTTPException 409: Username is already taken.
    """
    repo = UsersRepo(session)
    if await repo.get_by_field("username", payload.username) is not None:
        logger.warning(
            f"Registration rejected: username '{payload.username}' taken."
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="username already taken",
        )
    user = Users(
        user_id=uuid4(),
        username=payload.username,
        passwd_hash=hash_password(payload.password),
        has_usable_password=True,
        role=await _resolve_role(session, payload.username),
    )
    token_row, raw_token = _new_token_row(user.user_id, payload.client_type)
    user.auth_tokens.append(token_row)
    session.add(user)
    await session.commit()
    logger.info(f"User registered: user_id={user.user_id} role={user.role}.")
    return RegisterResponse(user_id=user.user_id, token=raw_token)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: _SessionDep,
) -> TokenResponse:
    """Issue a fresh token for username + password.

    Args:
        payload: Login credentials.
        session: Request-scoped database session.

    Returns:
        One-time plaintext token.

    Raises:
        HTTPException 401: Unknown user or wrong password (generic
            message on purpose — no oracle about which part failed).
    """
    repo = UsersRepo(session)
    user = await repo.get_by_field("username", payload.username)
    if user is None:
        verify_password_dummy(payload.password)
        logger.warning(f"Failed login for '{payload.username}'.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    # Auto-registered TG users have unusable password (None=default True).
    if getattr(user, "has_usable_password", True) is False:
        logger.warning(f"Failed login for '{payload.username}'.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    if not verify_password(payload.password, user.passwd_hash):
        logger.warning(f"Failed login for '{payload.username}'.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    token_row, raw_token = _new_token_row(user.user_id, payload.client_type)
    session.add(token_row)
    await session.commit()
    return TokenResponse(token=raw_token)


@router.post("/telegram/code", response_model=LinkCodeResponse)
async def create_telegram_link_code(
    current_user: _CurrentUserDep,
    request: Request,
) -> LinkCodeResponse:
    """Issue a one-time code to pair a Telegram account.

    The account making the call must already be authenticated; the
    code is then consumed by sending `/link <code>` to the bot from
    the Telegram account being linked.

    Args:
        current_user: Authenticated user requesting the pairing.
        request: Incoming request (provides app.state.redis).

    Returns:
        The pairing code and its lifetime in seconds.
    """
    redis_client: RedisClient = request.app.state.redis
    code = await redis_client.issue_link_code(current_user.user_id)
    logger.info(f"Pairing code issued for user_id={current_user.user_id}.")
    return LinkCodeResponse(code=code, expires_in=LINK_TTL_SECONDS)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current_user: _CurrentUserDep,
    session: _SessionDep,
    request: Request,
) -> Response:
    """Revoke the current bearer token.

    Args:
        current_user: Authenticated user.
        session: Database session.
        request: Request with Authorization header.

    Returns:
        Empty 204 response.
    """
    from app.brain.repositories.security import hash_token

    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if token:
        token_hash = hash_token(token)
        result = await session.execute(
            select(AuthTokens).where(
                AuthTokens.user_id == current_user.user_id,
                AuthTokens.token_hash == token_hash,
                AuthTokens.revoked_at.is_(None),
            )
        )
        token_row = result.scalar_one_or_none()
        if token_row is not None:
            token_row.revoked_at = datetime.now(UTC)
            await session.commit()
            logger.info(f"Token revoked for user_id={current_user.user_id}.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current_user: _CurrentUserDep,
    session: _SessionDep,
) -> Response:
    """Hard-delete the account and all of its data (GDPR-style).

    Cascades via FK ON DELETE CASCADE: auth_tokens, messages,
    hard_facts, audit_logs. Requires a valid bearer token.

    Args:
        current_user: Authenticated user to remove.
        session: Request-scoped database session.

    Returns:
        Empty 204 response.
    """
    await session.delete(current_user)
    await session.commit()
    logger.info(f"Account deleted: user_id={current_user.user_id}.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _resolve_role(session: AsyncSession, username: str) -> str:
    """Resolve the role for a new registration.

    Owner is only created via bootstrap CLI (ADR-16); public
    registration always yields ``user`` to avoid TOCTOU race and
    username hijacking via public OWNER_USERNAME.

    Args:
        session: Database session (unused, kept for signature).
        username: Username being registered.

    Returns:
        Always ``"user"``.
    """
    _ = session, username
    return "user"


def _new_token_row(user_id: UUID, client_type: str) -> tuple[AuthTokens, str]:
    """Build an AuthTokens row storing only the SHA-256 hash.

    Args:
        user_id: Owner of the token.
        client_type: "browser" or "cli".

    Returns:
        Tuple of (unsaved AuthTokens row, one-time plaintext token).
    """
    raw_token = generate_token()
    row = AuthTokens(
        user_id=user_id,
        client_type=client_type,
        token_hash=hash_token(raw_token),
        last_used_at=datetime.now(UTC),
    )
    return row, raw_token
