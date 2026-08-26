"""Unit tests for ``app.api.auth.schemas``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.auth.schemas import (
    LinkCodeResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
)


class TestRegisterRequest:
    """Validate RegisterRequest field constraints."""

    def test_valid_construction(self) -> None:
        req = RegisterRequest(username="testuser", password="securepass123")
        assert req.username == "testuser"
        assert req.client_type == "browser"

    def test_username_too_short(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(username="ab", password="securepass123")

    def test_username_too_long(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(username="a" * 21, password="securepass123")

    def test_username_invalid_chars(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(username="user name!", password="securepass123")

    def test_username_underscore_allowed(self) -> None:
        req = RegisterRequest(username="user_name", password="securepass123")
        assert req.username == "user_name"

    def test_username_digits_allowed(self) -> None:
        req = RegisterRequest(username="user123", password="securepass123")
        assert req.username == "user123"

    def test_password_too_short(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(username="testuser", password="short")

    def test_password_too_long(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(username="testuser", password="x" * 129)

    def test_client_type_cli(self) -> None:
        req = RegisterRequest(
            username="testuser",
            password="securepass123",
            client_type="cli",
        )
        assert req.client_type == "cli"


class TestLoginRequest:
    """Validate LoginRequest field constraints."""

    def test_valid_construction(self) -> None:
        req = LoginRequest(username="testuser", password="pass12345")
        assert req.username == "testuser"

    def test_password_max_length(self) -> None:
        req = LoginRequest(username="testuser", password="x" * 128)
        assert len(req.password) == 128

    def test_client_type_default(self) -> None:
        req = LoginRequest(username="testuser", password="pass12345")
        assert req.client_type == "browser"


class TestTokenResponse:
    def test_construction(self) -> None:
        resp = TokenResponse(token="abc123")
        assert resp.token == "abc123"


class TestLinkCodeResponse:
    def test_construction(self) -> None:
        resp = LinkCodeResponse(code="ABCD1234", expires_in=600)
        assert resp.expires_in == 600
