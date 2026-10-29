"""Owner authentication, sessions and rate limiting."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brain.memory.short.redis import RedisClient
from app.brain.repositories import UsersRepo
from app.brain.repositories.security import (
    generate_token,
    hash_token,
    verify_password,
    verify_password_dummy,
)

SESSION_COOKIE = "nodya_admin_session"
MAX_LOGIN_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
_USERNAME_MAX_LENGTH = 64
_GENERIC_ERROR = "Неверный логин или пароль."


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated panel operator."""

    username: str
    display_name: str | None = None
    role: str = "owner"


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    """Result of one login attempt."""

    principal: Principal | None = None
    token: str | None = None
    error: str | None = None
    rate_limited: bool = False


def hash_client_ip(ip: str) -> str:
    """Derive a stable non-reversible key for the client IP.

    Args:
        ip: Raw peer address.

    Returns:
        Truncated SHA-256 hex digest.
    """
    return hashlib.sha256(ip.encode()).hexdigest()[:32]


def safe_next(target: str | None) -> str:
    """Restrict post-login redirects to local absolute paths.

    Args:
        target: Raw ``next`` value from the query or form.

    Returns:
        Safe path, defaulting to the dashboard.
    """
    if (
        not target
        or len(target) > 512
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
    ):
        return "/"
    return target


def origin_is_allowed(
    origin: str | None,
    expected: str,
    host: str | None = None,
    sec_fetch_site: str | None = None,
) -> bool:
    """Decide whether a form POST may proceed.

    The Origin must match the configured public origin or the host the
    request was actually addressed to (same-origin), so the panel works
    over localhost, 127.0.0.1 or a tunnel without reconfiguring.

    An opaque ``null`` origin (sandboxed document, file://, embedded
    webview or a non-browser client) is accepted unless the browser
    itself labels the request as cross-site via ``Sec-Fetch-Site``. A
    missing header (curl, old clients) is tolerated because
    SameSite=Lax already blocks cross-site cookies.

    Args:
        origin: Value of the Origin header, if any.
        expected: Configured ADMIN_PUBLIC_ORIGIN.
        host: Host header of the request, if any.
        sec_fetch_site: Value of the Sec-Fetch-Site header, if any.

    Returns:
        True when the request may proceed.
    """
    if origin is None:
        return True
    if origin == "null":
        return sec_fetch_site not in ("cross-site", "same-site")
    normalized = origin.rstrip("/")
    if normalized == expected.rstrip("/"):
        return True
    if host:
        parsed = urlsplit(normalized)
        if parsed.scheme in ("http", "https") and parsed.netloc == host:
            return True
    return False


async def authenticate(
    session_factory: async_sessionmaker[AsyncSession],
    redis: RedisClient,
    username: str,
    password: str,
    ip_hash: str,
    ttl_seconds: int,
) -> LoginOutcome:
    """Verify owner credentials and open a Redis-backed session.

    Unknown usernames and wrong roles run a dummy password verify so
    the response time does not reveal whether an account exists.

    Args:
        session_factory: Session factory for the users lookup.
        redis: Shared Redis client.
        username: Submitted login name.
        password: Submitted plaintext password.
        ip_hash: Hashed client IP used by the failure counter.
        ttl_seconds: Session lifetime in Redis and the cookie.

    Returns:
        Outcome with a session token or a user-facing error.

    Raises:
        Exception: Redis or PostgreSQL failures propagate so the
            route can fail closed.
    """
    failures = await redis.get_login_failures(ip_hash)
    if failures >= MAX_LOGIN_FAILURES:
        return LoginOutcome(
            error="Слишком много неудачных попыток. Повторите через 15 минут.",
            rate_limited=True,
        )

    name = username.strip()[:_USERNAME_MAX_LENGTH]
    if not name or not password:
        await redis.register_login_failure(
            ip_hash, LOGIN_FAILURE_WINDOW_SECONDS
        )
        return LoginOutcome(error=_GENERIC_ERROR)

    async with session_factory() as session:
        user = await UsersRepo(session).get_by_field("username", name)

    if user is None:
        await asyncio.to_thread(verify_password_dummy, password)
        await redis.register_login_failure(
            ip_hash, LOGIN_FAILURE_WINDOW_SECONDS
        )
        return LoginOutcome(error=_GENERIC_ERROR)

    valid = await asyncio.to_thread(
        verify_password, password, user.passwd_hash
    )
    if not valid or user.role != "owner":
        await redis.register_login_failure(
            ip_hash, LOGIN_FAILURE_WINDOW_SECONDS
        )
        return LoginOutcome(error=_GENERIC_ERROR)

    await redis.reset_login_failures(ip_hash)
    token = generate_token()
    principal = Principal(
        username=user.username,
        display_name=user.display_name,
        role="owner",
    )
    payload = {
        "username": principal.username,
        "display_name": principal.display_name,
        "role": principal.role,
        "created_at": datetime.now(UTC).isoformat(),
    }
    await redis.set_admin_session(
        hash_token(token),
        payload,
        ttl_seconds,
    )
    return LoginOutcome(principal=principal, token=token)


async def load_principal(redis: RedisClient, token: str) -> Principal | None:
    """Resolve a session cookie into a principal.

    Args:
        redis: Shared Redis client.
        token: Raw cookie value.

    Returns:
        The owner principal, or None for unknown/expired sessions.

    Raises:
        Exception: Redis failures propagate so callers can fail
            closed with a 503.
    """
    token_hash = hash_token(token)
    payload = await redis.get_admin_session(token_hash)
    if payload is None:
        return None
    username = payload.get("username")
    role = payload.get("role")
    if role != "owner" or not isinstance(username, str) or not username:
        await redis.delete_admin_session(token_hash)
        return None
    display_name = payload.get("display_name")
    return Principal(
        username=username,
        display_name=display_name if isinstance(display_name, str) else None,
        role="owner",
    )


async def destroy_session(redis: RedisClient, token: str) -> None:
    """Delete a session from the store.

    Args:
        redis: Shared Redis client.
        token: Raw cookie value.

    Raises:
        Exception: Redis failures propagate to the caller.
    """
    await redis.delete_admin_session(hash_token(token))
