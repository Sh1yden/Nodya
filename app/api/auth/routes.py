"""Регистрация и логин: Users + AuthTokens.

Пароли — argon2id, opaque-токены — SHA-256 (ADR-3). Роль owner:
первый зарегистрировавшийся либо совпадение с OWNER_USERNAME
(поправка 8).
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.brain.memory.long.database import get_db
from app.brain.models import AuthTokens, Users
from app.brain.repositories import UsersRepo
from app.brain.repositories.security import (
    generate_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.core import get_logger, settings

from .schemas import (
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
    """Создать пользователя и выдать первый токен."""
    repo = UsersRepo(session)
    if await repo.get_by_field("username", payload.username) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="username already taken",
        )
    user = Users(
        user_id=uuid4(),
        username=payload.username,
        passwd_hash=hash_password(payload.password),
        role=await _resolve_role(session, payload.username),
    )
    token_row, raw_token = _new_token_row(user.user_id, payload.client_type)
    user.auth_tokens.append(token_row)
    session.add(user)
    await session.commit()
    logger.info("Зарегистрирован user_id=%s role=%s.", user.user_id, user.role)
    return RegisterResponse(user_id=user.user_id, token=raw_token)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: _SessionDep,
) -> TokenResponse:
    """Выдать новый токен по username+password."""
    repo = UsersRepo(session)
    user = await repo.get_by_field("username", payload.username)
    if user is None or not verify_password(payload.password, user.passwd_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    token_row, raw_token = _new_token_row(user.user_id, payload.client_type)
    session.add(token_row)
    await session.commit()
    return TokenResponse(token=raw_token)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current_user: _CurrentUserDep,
    session: _SessionDep,
) -> Response:
    """Полное удаление аккаунта и всех его данных (GDPR-style).

    Каскад по FK ON DELETE CASCADE: auth_tokens, messages,
    hard_facts, audit_logs. Токен в заголовке обязателен.
    """
    await session.delete(current_user)
    await session.commit()
    logger.info("Аккаунт удалён: user_id=%s.", current_user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _resolve_role(session: AsyncSession, username: str) -> str:
    """owner: первый пользователь деплоя или OWNER_USERNAME."""
    total = await session.scalar(select(func.count()).select_from(Users))
    if total == 0 or username == settings.OWNER_USERNAME:
        return "owner"
    return "user"


def _new_token_row(user_id: UUID, client_type: str) -> tuple[AuthTokens, str]:
    """AuthTokens с sha256-хэшем; plaintext возвращается один раз."""
    raw_token = generate_token()
    row = AuthTokens(
        user_id=user_id,
        client_type=client_type,
        token_hash=hash_token(raw_token),
        last_used_at=datetime.now(UTC),
    )
    return row, raw_token
