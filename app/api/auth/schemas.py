"""Request/response schemas for authentication."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """Create an account and the first token."""

    username: str = Field(
        min_length=3,
        max_length=20,
        pattern=r"^[a-zA-Z0-9_]+$",
    )
    password: str = Field(min_length=8, max_length=128)
    client_type: Literal["browser", "cli"] = "browser"


class LoginRequest(BaseModel):
    """Obtain a new token with username + password."""

    username: str = Field(min_length=3, max_length=20)
    password: str = Field(max_length=128)
    client_type: Literal["browser", "cli"] = "browser"


class TokenResponse(BaseModel):
    """Plaintext token returned exactly once (ADR-3)."""

    token: str


class RegisterResponse(TokenResponse):
    """Registration result: token plus the created user id."""

    user_id: UUID
