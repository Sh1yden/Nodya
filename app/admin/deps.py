"""FastAPI dependencies shared by admin routers."""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.admin.auth import (
    SESSION_COOKIE,
    Principal,
    load_principal,
    origin_is_allowed,
)
from app.admin.services import AdminServices
from app.core import get_logger

logger = get_logger(__name__)


class NotAuthenticated(Exception):
    """Raised when a protected page has no valid session cookie."""


def get_services(request: Request) -> AdminServices:
    """Return the services instance created by the lifespan.

    Args:
        request: Incoming request.

    Returns:
        Initialized AdminServices.

    Raises:
        HTTPException: The app is not fully started.
    """
    services: AdminServices | None = getattr(request.app.state, "admin", None)
    if services is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервисы панели ещё не инициализированы.",
        )
    return services


ServicesDep = Annotated[AdminServices, Depends(get_services)]


def render_template(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    """Render a Jinja template with the request attached.

    Args:
        request: Incoming request.
        name: Template name inside the admin templates directory.
        context: Extra template variables.
        status_code: HTTP status of the response.

    Returns:
        Rendered HTML response.
    """
    return request.app.state.templates.TemplateResponse(
        request,
        name,
        context or {},
        status_code=status_code,
    )


async def require_owner(
    request: Request,
    services: ServicesDep,
) -> Principal:
    """Authenticate the request against the Redis session store.

    Use through the ``PrincipalDep`` alias so FastAPI injects the
    services instance.

    Fails closed with 503 when Redis is unavailable: the panel never
    falls back to DB lookups per request (ADR: Redis-authoritative
    sessions).

    Args:
        request: Incoming request.
        services: Injected services facade.

    Returns:
        The authenticated owner principal.

    Raises:
        NotAuthenticated: No or expired session.
        HTTPException: Session store is down (503).
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise NotAuthenticated
    try:
        principal = await load_principal(services.redis, token)
    except Exception as exc:
        logger.warning(f"Admin session store unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Хранилище сессий недоступно.",
        ) from exc
    if principal is None:
        raise NotAuthenticated
    request.state.principal = principal
    return principal


def verify_origin(
    request: Request,
    services: ServicesDep,
) -> None:
    """Reject cross-origin form submissions.

    Args:
        request: Incoming POST request.
        services: Injected services facade.

    Raises:
        HTTPException: Origin differs from ADMIN_PUBLIC_ORIGIN (403).
    """
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if not origin_is_allowed(
        origin, services.settings.ADMIN_PUBLIC_ORIGIN, host, sec_fetch_site
    ):
        logger.warning(
            "Admin origin rejected: "
            f"origin={origin!r} host={host!r} "
            f"sec-fetch-site={sec_fetch_site!r} "
            f"sec-fetch-mode={request.headers.get('sec-fetch-mode')!r} "
            f"referer={request.headers.get('referer')!r} "
            f"user-agent={request.headers.get('user-agent')!r}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Запрос с чужого origin отклонён.",
        )


ServicesDep = Annotated[AdminServices, Depends(get_services)]
PrincipalDep = Annotated[Principal, Depends(require_owner)]
ServicesDep = Annotated[AdminServices, Depends(get_services)]
PrincipalDep = Annotated[Principal, Depends(require_owner)]
OriginCheck = Annotated[None, Depends(verify_origin)]
