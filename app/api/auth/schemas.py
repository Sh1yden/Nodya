"""Схемы запросов/ответов аутентификации."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """Создание аккаунта и первого токена."""

    username: str = Field(
        min_length=3,
        max_length=20,
        pattern=r"^[a-zA-Z0-9_]+$",
    )
    password: str = Field(min_length=8, max_length=128)
    client_type: Literal["browser", "cli"] = "browser"


class LoginRequest(BaseModel):
    """Получение нового токена по паролю."""

    username: str = Field(min_length=3, max_length=20)
    password: str = Field(max_length=128)
    client_type: Literal["browser", "cli"] = "browser"


class TokenResponse(BaseModel):
    """Plaintext-токен отдаётся только один раз (ADR-3)."""

    token: str


class RegisterResponse(TokenResponse):
    user_id: uuid.UUID
