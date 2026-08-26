"""Integration tests for ``app.api.auth.routes``."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth.routes import router
from app.brain.memory.long import get_db
from app.brain.models import Users


@pytest.fixture()
def app(mock_session: AsyncMock) -> FastAPI:
    app = FastAPI()

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield mock_session

    app.dependency_overrides[get_db] = _override_db
    app.include_router(router)
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _make_user(
    uid: uuid.UUID | None = None,
    username: str = "testuser",
    telegram_id: int = 12345,
) -> Users:
    return Users(
        user_id=uid or uuid.uuid4(),
        telegram_id=telegram_id,
        username=username,
        passwd_hash="$argon2id$v=19$m=65536$test$hash",
        role="user",
    )


class TestRegisterEndpoint:
    @patch("app.api.auth.routes.UsersRepo")
    @patch("app.api.auth.routes.hash_password")
    @patch("app.api.auth.routes.generate_token")
    @patch("app.api.auth.routes.hash_token")
    def test_register_success(
        self,
        mock_hash_token: Mock,
        mock_gen_token: Mock,
        mock_hash_pw: Mock,
        mock_repo_cls: Mock,
        client: TestClient,
        mock_session: AsyncMock,
    ) -> None:
        mock_hash_pw.return_value = "hashed_pw"
        mock_gen_token.return_value = "raw-token-abc"
        mock_hash_token.return_value = "token-hash"

        mock_repo = MagicMock()
        mock_repo.get_by_field = AsyncMock(return_value=None)
        mock_repo_cls.return_value = mock_repo

        response = client.post(
            "/auth/register",
            json={
                "username": "newuser",
                "password": "securepass123",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "token" in data
        assert "user_id" in data

    @patch("app.api.auth.routes.UsersRepo")
    def test_register_duplicate_username(
        self,
        mock_repo_cls: Mock,
        client: TestClient,
    ) -> None:
        existing = _make_user()
        mock_repo = MagicMock()
        mock_repo.get_by_field = AsyncMock(return_value=existing)
        mock_repo_cls.return_value = mock_repo

        response = client.post(
            "/auth/register",
            json={
                "username": "existing",
                "password": "securepass123",
            },
        )
        assert response.status_code == 409


class TestLoginEndpoint:
    @patch("app.api.auth.routes.UsersRepo")
    @patch("app.api.auth.routes.verify_password")
    @patch("app.api.auth.routes.generate_token")
    @patch("app.api.auth.routes.hash_token")
    def test_login_success(
        self,
        mock_hash_token: Mock,
        mock_gen_token: Mock,
        mock_verify: Mock,
        mock_repo_cls: Mock,
        client: TestClient,
        mock_session: AsyncMock,
    ) -> None:
        mock_verify.return_value = True
        mock_gen_token.return_value = "raw-token"
        mock_hash_token.return_value = "hash"

        user = _make_user()
        mock_repo = MagicMock()
        mock_repo.get_by_field = AsyncMock(return_value=user)
        mock_repo_cls.return_value = mock_repo

        response = client.post(
            "/auth/login",
            json={
                "username": "testuser",
                "password": "correctpass",
            },
        )
        assert response.status_code == 200
        assert "token" in response.json()

    @patch("app.api.auth.routes.UsersRepo")
    @patch("app.api.auth.routes.verify_password")
    def test_login_wrong_password(
        self,
        mock_verify: Mock,
        mock_repo_cls: Mock,
        client: TestClient,
    ) -> None:
        mock_verify.return_value = False

        user = _make_user()
        mock_repo = MagicMock()
        mock_repo.get_by_field = AsyncMock(return_value=user)
        mock_repo_cls.return_value = mock_repo

        response = client.post(
            "/auth/login",
            json={
                "username": "testuser",
                "password": "wrongpass",
            },
        )
        assert response.status_code == 401

    @patch("app.api.auth.routes.UsersRepo")
    def test_login_user_not_found(
        self,
        mock_repo_cls: Mock,
        client: TestClient,
    ) -> None:
        mock_repo = MagicMock()
        mock_repo.get_by_field = AsyncMock(return_value=None)
        mock_repo_cls.return_value = mock_repo

        response = client.post(
            "/auth/login",
            json={
                "username": "nobody",
                "password": "pass12345",
            },
        )
        assert response.status_code == 401


class TestDeleteMeEndpoint:
    def test_delete_me_success(
        self,
        client: TestClient,
        mock_session: AsyncMock,
        app: FastAPI,
    ) -> None:
        user = _make_user()
        from app.api.deps import get_current_user

        async def _override_user() -> Users:
            return user

        app.dependency_overrides[get_current_user] = _override_user

        response = client.delete(
            "/auth/me",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert response.status_code == 204
