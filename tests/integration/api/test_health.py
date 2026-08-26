"""Integration tests for ``app.api.health``."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import router


@pytest.fixture()
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoint:
    @patch("app.api.health.ping_qdrant", new_callable=AsyncMock)
    @patch("app.api.health.ping_rabbitmq", new_callable=AsyncMock)
    @patch("app.api.health.ping_postgres", new_callable=AsyncMock)
    def test_all_healthy_returns_200(
        self,
        mock_pg: AsyncMock,
        mock_rmq: AsyncMock,
        mock_qd: AsyncMock,
        client: TestClient,
    ) -> None:
        mock_pg.return_value = True
        mock_rmq.return_value = True
        mock_qd.return_value = True

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["healthy"] is True
        assert data["postgres"] is True
        assert data["rabbitmq"] is True
        assert data["qdrant"] is True

    @patch("app.api.health.ping_qdrant", new_callable=AsyncMock)
    @patch("app.api.health.ping_rabbitmq", new_callable=AsyncMock)
    @patch("app.api.health.ping_postgres", new_callable=AsyncMock)
    def test_postgres_down_returns_503(
        self,
        mock_pg: AsyncMock,
        mock_rmq: AsyncMock,
        mock_qd: AsyncMock,
        client: TestClient,
    ) -> None:
        mock_pg.return_value = False
        mock_rmq.return_value = True
        mock_qd.return_value = True

        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["healthy"] is False
        assert data["postgres"] is False

    @patch("app.api.health.ping_qdrant", new_callable=AsyncMock)
    @patch("app.api.health.ping_rabbitmq", new_callable=AsyncMock)
    @patch("app.api.health.ping_postgres", new_callable=AsyncMock)
    def test_all_down_returns_503(
        self,
        mock_pg: AsyncMock,
        mock_rmq: AsyncMock,
        mock_qd: AsyncMock,
        client: TestClient,
    ) -> None:
        mock_pg.return_value = False
        mock_rmq.return_value = False
        mock_qd.return_value = False

        response = client.get("/health")
        assert response.status_code == 503
