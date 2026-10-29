"""Unit tests for admin authentication helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.admin import auth as auth_module
from app.admin.auth import (
    MAX_LOGIN_FAILURES,
    Principal,
    authenticate,
    destroy_session,
    hash_client_ip,
    load_principal,
    origin_is_allowed,
    safe_next,
)
from app.brain.repositories.security import hash_token


def _redis(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(
        get_login_failures=AsyncMock(return_value=kwargs.get("failures", 0)),
        register_login_failure=AsyncMock(return_value=1),
        reset_login_failures=AsyncMock(),
        set_admin_session=AsyncMock(),
        get_admin_session=AsyncMock(return_value=kwargs.get("session")),
        delete_admin_session=AsyncMock(),
    )


class _FakeContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


def _session_factory() -> _FakeContext:
    return _FakeContext()


def _user(
    username: str = "owner",
    role: str = "owner",
    display_name: str | None = "Owner",
) -> SimpleNamespace:
    return SimpleNamespace(
        username=username,
        role=role,
        display_name=display_name,
        passwd_hash="$argon2id$fake",
    )


@pytest.fixture()
def repo(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    fake = SimpleNamespace(get_by_field=AsyncMock(return_value=None))
    monkeypatch.setattr(auth_module, "UsersRepo", lambda session: fake)
    return fake


@pytest.fixture()
def passwords(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    dummy_calls: list[str] = []

    def fake_verify(password: str, password_hash: str) -> bool:
        return password == "correct"

    monkeypatch.setattr(auth_module, "verify_password", fake_verify)
    monkeypatch.setattr(
        auth_module,
        "verify_password_dummy",
        lambda password: dummy_calls.append(password),
    )
    return dummy_calls


def test_hash_client_ip_is_stable_and_salted() -> None:
    first = hash_client_ip("203.0.113.7")
    assert first == hash_client_ip("203.0.113.7")
    assert first != hash_client_ip("203.0.113.8")
    assert len(first) == 32
    assert "203.0.113.7" not in first


def test_safe_next_blocks_open_redirects() -> None:
    assert safe_next("/containers/app") == "/containers/app"
    assert safe_next(None) == "/"
    assert safe_next("") == "/"
    assert safe_next("//evil.example") == "/"
    assert safe_next("https://evil.example") == "/"
    assert safe_next("\\\\evil.example") == "/"
    assert safe_next("/" + "a" * 600) == "/"


def test_origin_is_allowed() -> None:
    expected = "https://admin.nodya.shayden.ru"
    assert origin_is_allowed(None, expected)
    assert origin_is_allowed("https://admin.nodya.shayden.ru", expected)
    assert origin_is_allowed("https://admin.nodya.shayden.ru/", expected)
    assert not origin_is_allowed("https://evil.example", expected)
    assert not origin_is_allowed("http://admin.nodya.shayden.ru", expected)


def test_origin_is_allowed_for_same_host() -> None:
    expected = "http://localhost:8016"
    assert origin_is_allowed(
        "http://localhost:8016", expected, "localhost:8016"
    )
    assert origin_is_allowed(
        "http://127.0.0.1:8016", expected, "127.0.0.1:8016"
    )
    assert origin_is_allowed(
        "https://admin.nodya.shayden.ru", expected, "admin.nodya.shayden.ru"
    )
    assert not origin_is_allowed(
        "http://localhost:9998", expected, "localhost:9999"
    )
    assert not origin_is_allowed(
        "https://evil.example", expected, "localhost:8016"
    )
    assert origin_is_allowed("null", expected, "localhost:8016", None)
    assert origin_is_allowed("null", expected, "localhost:8016", "same-origin")
    assert not origin_is_allowed(
        "null", expected, "localhost:8016", "cross-site"
    )
    assert not origin_is_allowed(
        "null", expected, "localhost:8016", "same-site"
    )


async def test_authenticate_success_opens_session(
    repo: SimpleNamespace,
    passwords: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo.get_by_field.return_value = _user()
    redis = _redis()
    outcome = await authenticate(
        _session_factory,
        redis,
        "owner",
        "correct",
        "ip-hash",
        600,
    )
    assert outcome.error is None
    assert outcome.token is not None
    assert outcome.principal == Principal(
        username="owner", display_name="Owner", role="owner"
    )
    redis.reset_login_failures.assert_awaited_once_with("ip-hash")
    args = redis.set_admin_session.await_args
    assert args.args[0] == hash_token(outcome.token)
    assert args.args[0] != outcome.token
    assert args.args[1]["role"] == "owner"
    assert args.args[2] == 600


async def test_authenticate_wrong_password_fails_generic(
    repo: SimpleNamespace, passwords: list[str]
) -> None:
    repo.get_by_field.return_value = _user()
    redis = _redis()
    outcome = await authenticate(
        _session_factory, redis, "owner", "nope", "ip-hash", 600
    )
    assert outcome.error == "Неверный логин или пароль."
    assert outcome.token is None
    redis.register_login_failure.assert_awaited_once()
    redis.set_admin_session.assert_not_awaited()


async def test_authenticate_unknown_user_runs_dummy_verify(
    repo: SimpleNamespace, passwords: list[str]
) -> None:
    repo.get_by_field.return_value = None
    redis = _redis()
    outcome = await authenticate(
        _session_factory, redis, "ghost", "whatever", "ip-hash", 600
    )
    assert outcome.error is not None
    assert passwords == ["whatever"]
    redis.register_login_failure.assert_awaited_once()


async def test_authenticate_rejects_non_owner(
    repo: SimpleNamespace, passwords: list[str]
) -> None:
    repo.get_by_field.return_value = _user(role="user")
    redis = _redis()
    outcome = await authenticate(
        _session_factory, redis, "guest", "correct", "ip-hash", 600
    )
    assert outcome.error is not None
    assert outcome.token is None
    redis.set_admin_session.assert_not_awaited()


async def test_authenticate_rate_limited_before_db(
    repo: SimpleNamespace, passwords: list[str]
) -> None:
    redis = _redis(failures=MAX_LOGIN_FAILURES)
    outcome = await authenticate(
        _session_factory, redis, "owner", "correct", "ip-hash", 600
    )
    assert outcome.rate_limited
    assert outcome.token is None
    repo.get_by_field.assert_not_awaited()


async def test_authenticate_empty_form_registers_failure(
    repo: SimpleNamespace, passwords: list[str]
) -> None:
    redis = _redis()
    outcome = await authenticate(
        _session_factory, redis, "  ", "", "ip-hash", 600
    )
    assert outcome.error is not None
    redis.register_login_failure.assert_awaited_once()
    repo.get_by_field.assert_not_awaited()


async def test_load_principal_valid_session() -> None:
    redis = _redis(
        session={
            "username": "owner",
            "display_name": "Owner",
            "role": "owner",
        }
    )
    principal = await load_principal(redis, "raw-token")
    assert principal == Principal("owner", "Owner", "owner")
    redis.get_admin_session.assert_awaited_once_with(hash_token("raw-token"))


async def test_load_principal_drops_wrong_role() -> None:
    redis = _redis(session={"username": "owner", "role": "user"})
    assert await load_principal(redis, "raw-token") is None
    redis.delete_admin_session.assert_awaited_once()


async def test_load_principal_missing_session() -> None:
    redis = _redis(session=None)
    assert await load_principal(redis, "raw-token") is None
    redis.delete_admin_session.assert_not_awaited()


async def test_destroy_session_hashes_token() -> None:
    redis = _redis()
    await destroy_session(redis, "raw-token")
    redis.delete_admin_session.assert_awaited_once_with(
        hash_token("raw-token")
    )
