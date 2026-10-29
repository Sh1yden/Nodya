"""Login and logout routes."""

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.admin.auth import (
    SESSION_COOKIE,
    authenticate,
    destroy_session,
    hash_client_ip,
    load_principal,
    safe_next,
)
from app.admin.deps import (
    OriginCheck,
    ServicesDep,
    render_template,
)
from app.core import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _render_login(
    request: Request,
    *,
    next_target: str = "/",
    error: str | None = None,
    rate_limited: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the login page with an optional error.

    Args:
        request: Incoming request.
        next_target: Raw post-login path.
        error: User-facing error text.
        rate_limited: Whether the failure window is exhausted.
        status_code: HTTP status to return.

    Returns:
        Rendered login page.
    """
    return render_template(
        request,
        "login.html",
        {
            "principal": None,
            "next": safe_next(next_target),
            "error": error,
            "rate_limited": rate_limited,
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    services: ServicesDep,
    next: Annotated[str, Query()] = "/",
) -> HTMLResponse:
    """Show the login form; already authenticated users skip it.

    Args:
        request: Incoming request.
        next: Path to continue after login.
        services: Injected services facade.

    Returns:
        Login page or a redirect to the dashboard.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        try:
            if await load_principal(services.redis, token) is not None:
                return RedirectResponse("/", status_code=303)
        except Exception:
            pass
    return _render_login(request, next_target=next)


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    services: ServicesDep,
    origin_checked: OriginCheck,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse:
    """Check credentials and open a session cookie.

    Args:
        request: Incoming request.
        services: Injected services facade.
        origin_checked: Enforces the same-origin POST rule.
        username: Submitted login name.
        password: Submitted plaintext password.
        next: Path to continue after login.

    Returns:
        Redirect on success, login page with an error otherwise.
    """
    client_ip = request.client.host if request.client else "unknown"
    ttl = services.settings.ADMIN_SESSION_TTL_MINUTES * 60
    try:
        outcome = await authenticate(
            services.session_factory,
            services.redis,
            username,
            password,
            hash_client_ip(client_ip),
            ttl,
        )
    except Exception as exc:
        logger.warning(f"Admin login failed closed: {exc}")
        return _render_login(
            request,
            next_target=next,
            error="Сервис авторизации временно недоступен.",
            status_code=503,
        )
    if outcome.rate_limited:
        return _render_login(
            request,
            next_target=next,
            error=outcome.error,
            rate_limited=True,
            status_code=429,
        )
    if outcome.error or outcome.token is None:
        return _render_login(
            request,
            next_target=next,
            error=outcome.error,
            status_code=401,
        )
    response = RedirectResponse(safe_next(next), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        outcome.token,
        max_age=ttl,
        httponly=True,
        secure=services.settings.ADMIN_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    services: ServicesDep,
    origin_checked: OriginCheck,
) -> RedirectResponse:
    """Drop the session from Redis and clear the cookie.

    Args:
        request: Incoming request.
        services: Injected services facade.
        origin_checked: Enforces the same-origin POST rule.

    Returns:
        Redirect to the login page.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        try:
            await destroy_session(services.redis, token)
        except Exception as exc:
            logger.warning(f"Admin session delete failed: {exc}")
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
