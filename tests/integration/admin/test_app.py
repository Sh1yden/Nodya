"""Integration tests for the admin app with stubbed services."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.admin.auth import SESSION_COOKIE, LoginOutcome, Principal
from app.admin.collectors import (
    ContainerInfo,
    ContainerLogLine,
    DependencyStatus,
    ErrorRecord,
    LogEntry,
    LogFileInfo,
    LogSlice,
    PostgresStats,
    QdrantCollectionInfo,
    QueueInfo,
    RedisStats,
    Result,
    RuntimeStatus,
)
from app.admin.main import create_app

OWNER = Principal(username="owner", display_name="Owner")
FINGERPRINT = "a" * 64


def _error_record(message: str = "worker crashed") -> ErrorRecord:
    now = datetime.now(UTC)
    return ErrorRecord(
        fingerprint=FINGERPRINT,
        level="ERROR",
        logger="nodya.worker",
        module="worker",
        function="_flush",
        exception_type="RuntimeError",
        message=message,
        traceback="Traceback (most recent call last):",
        count=4,
        first_seen=now,
        last_seen=now,
    )


def _container() -> ContainerInfo:
    return ContainerInfo(
        id="abcdef1234567890",
        name="nodya_heart",
        service="app",
        image="nodya-app:local",
        state="running",
        status="Up 2 hours",
        health="healthy",
        started_at=datetime(2026, 10, 29, 10, 0, tzinfo=UTC),
        restart_count=0,
        ports=("8014->8014/tcp",),
    )


class FakeServices:
    """In-memory stand-in for AdminServices."""

    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            ADMIN_PUBLIC_ORIGIN="https://admin.nodya.shayden.ru",
            ADMIN_COOKIE_SECURE=True,
            ADMIN_SESSION_TTL_MINUTES=720,
        )
        self.redis = SimpleNamespace()
        self.session_factory = object()
        self.error_detail: ErrorRecord | None = _error_record()
        self.containers_data = [_container()]
        self.deps_ok = True

    async def get_deps(self) -> Result:
        return Result(
            data=[
                DependencyStatus("postgres", self.deps_ok),
                DependencyStatus("rabbitmq", True),
                DependencyStatus("redis", True),
                DependencyStatus("qdrant", True),
                DependencyStatus("docker", True),
            ]
        )

    async def get_runtime(self) -> Result:
        return Result(
            data=RuntimeStatus(
                "healthy",
                {
                    "pid": 42,
                    "version": "dev",
                    "started_at": "2026-10-29T10:00:00+00:00",
                    "heartbeat_at": "2026-10-29T12:00:00+00:00",
                    "jobs": [{"id": "consolidation", "next_run": None}],
                },
                3.0,
            )
        )

    async def get_containers(self) -> Result:
        return Result(data=self.containers_data)

    async def find_container(self, service: str) -> ContainerInfo | None:
        return next(
            (item for item in self.containers_data if item.service == service),
            None,
        )

    async def get_container_logs(
        self, container: ContainerInfo, tail: int = 200
    ) -> Result:
        return Result(
            data=[
                ContainerLogLine(datetime.now(UTC), "stdout", "hello <world>")
            ]
        )

    async def get_queues(self) -> Result:
        return Result(data=[QueueInfo("incoming_messages", 3, 1)])

    async def get_errors(
        self, limit: int = 50, level: str | None = None
    ) -> Result:
        records = [self.error_detail] if self.error_detail else []
        return Result(data=records)

    async def get_error(self, fingerprint: str) -> Result:
        if (
            self.error_detail is not None
            and fingerprint == self.error_detail.fingerprint
        ):
            return Result(data=self.error_detail)
        return Result(data=None)

    async def get_log_files(self) -> Result:
        return Result(
            data=[LogFileInfo("2026-10-29-01.jsonl", 100, datetime.now(UTC))]
        )

    async def get_logs(
        self,
        filename: str,
        level: str | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> Result:
        return Result(
            data=LogSlice(
                file=filename,
                entries=[
                    LogEntry(
                        "12:00:00.123",
                        "INFO",
                        "nodya.worker",
                        "hello <world>",
                    )
                ],
                truncated=False,
                scanned=1,
            )
        )

    async def get_postgres_stats(self) -> Result:
        return Result(data=PostgresStats(2048, 2))

    async def get_redis_stats(self) -> Result:
        return Result(data=RedisStats(1024, 3, 100, "7.4.0"))

    async def get_qdrant_stats(self) -> Result:
        return Result(data=[QdrantCollectionInfo("nodya_memory", 5, "green")])


@pytest.fixture()
def app() -> Any:
    return create_app()


@pytest.fixture()
def client(app: Any) -> Any:
    with TestClient(
        app, base_url="https://admin.nodya.shayden.ru"
    ) as test_client:
        app.state.admin = FakeServices()
        yield test_client


def _login(
    monkeypatch: pytest.MonkeyPatch,
    principal: Principal | None = OWNER,
    error: Exception | None = None,
) -> None:
    async def fake_principal(redis: Any, token: str) -> Principal | None:
        if error is not None:
            raise error
        return principal

    monkeypatch.setattr("app.admin.deps.load_principal", fake_principal)


def _set_session(client: TestClient) -> None:
    client.cookies.set(
        SESSION_COOKIE,
        "session-token",
        domain="admin.nodya.shayden.ru",
    )


def test_unauthenticated_page_redirects_to_login(
    client: TestClient,
) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/"


def test_unauthenticated_partial_asks_htmx_to_redirect(
    client: TestClient,
) -> None:
    response = client.get("/partials/containers")
    assert response.status_code == 401
    assert (
        response.headers["HX-Redirect"] == "/login?next=/partials/containers"
    )


def test_session_store_down_fails_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch, error=RuntimeError("redis down"))
    _set_session(client)
    response = client.get("/")
    assert response.status_code == 503


def test_dashboard_renders_with_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Обзор" in body
    assert "PostgreSQL" in body
    assert "incoming_messages" in body
    assert "nodya-app:local" in body
    assert response.headers["Cache-Control"] == "no-store"
    assert (
        "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    )
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_login_page_has_security_headers(client: TestClient) -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert "Войти" in response.text
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_login_success_sets_strict_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_auth(*args: Any, **kwargs: Any) -> LoginOutcome:
        return LoginOutcome(principal=OWNER, token="raw-token")

    monkeypatch.setattr("app.admin.routes.auth.authenticate", fake_auth)
    response = client.post(
        "/login",
        data={"username": "owner", "password": "secret"},
        headers={"Origin": "https://admin.nodya.shayden.ru"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Domain=" not in cookie


def test_login_rejects_cross_origin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_auth(*args: Any, **kwargs: Any) -> LoginOutcome:
        raise AssertionError("authenticate must not run")

    monkeypatch.setattr("app.admin.routes.auth.authenticate", fake_auth)
    response = client.post(
        "/login",
        data={"username": "owner", "password": "secret"},
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_login_invalid_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_auth(*args: Any, **kwargs: Any) -> LoginOutcome:
        return LoginOutcome(error="Неверный логин или пароль.")

    monkeypatch.setattr("app.admin.routes.auth.authenticate", fake_auth)
    response = client.post(
        "/login",
        data={"username": "owner", "password": "bad"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "Неверный логин или пароль." in response.text
    assert "set-cookie" not in response.headers


def test_login_rate_limited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_auth(*args: Any, **kwargs: Any) -> LoginOutcome:
        return LoginOutcome(
            error="Слишком много неудачных попыток.",
            rate_limited=True,
        )

    monkeypatch.setattr("app.admin.routes.auth.authenticate", fake_auth)
    response = client.post(
        "/login",
        data={"username": "owner", "password": "bad"},
        follow_redirects=False,
    )
    assert response.status_code == 429


def test_login_sanitizes_next(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_auth(*args: Any, **kwargs: Any) -> LoginOutcome:
        return LoginOutcome(principal=OWNER, token="raw-token")

    monkeypatch.setattr("app.admin.routes.auth.authenticate", fake_auth)
    response = client.post(
        "/login",
        data={
            "username": "owner",
            "password": "secret",
            "next": "//evil.example",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_logout_clears_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    destroyed: list[str] = []

    async def fake_destroy(redis: Any, token: str) -> None:
        destroyed.append(token)

    monkeypatch.setattr("app.admin.routes.auth.destroy_session", fake_destroy)
    _set_session(client)
    response = client.post(
        "/logout",
        headers={"Origin": "https://admin.nodya.shayden.ru"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert destroyed == ["session-token"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_healthz_needs_no_auth(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_readyz_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    client.app.state.admin.deps_ok = False
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["ready"] is False


def test_error_detail_escapes_html(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    client.app.state.admin.error_detail = _error_record(
        "<script>alert(1)</script>"
    )
    response = client.get(f"/errors/{FINGERPRINT}")
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_errors_page_lists_groups(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    response = client.get("/errors")
    assert response.status_code == 200
    assert "worker crashed" in response.text
    assert f"/errors/{FINGERPRINT}" in response.text


def test_logs_page_rejects_bad_filename(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    response = client.get("/logs", params={"file": "../../etc/passwd"})
    assert response.status_code == 404
    assert "Недопустимое имя файла" in response.text


def test_logs_page_escapes_messages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    response = client.get("/logs")
    assert response.status_code == 200
    assert "hello &lt;world&gt;" in response.text


def test_container_detail_renders_logs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    response = client.get("/containers/app")
    assert response.status_code == 200
    assert "nodya_heart" in response.text
    assert "hello &lt;world&gt;" in response.text
    assert "8014-&gt;8014/tcp" in response.text


def test_missing_container_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(monkeypatch)
    _set_session(client)
    response = client.get("/containers/unknown")
    assert response.status_code == 404
    assert "Контейнер не найден" in response.text


def test_docs_are_disabled(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_unknown_host_is_rejected(app: Any) -> None:
    with TestClient(app, base_url="https://evil.example") as client:
        response = client.get("/healthz")
    assert response.status_code == 400
