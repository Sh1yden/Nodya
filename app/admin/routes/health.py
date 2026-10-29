"""Liveness and readiness endpoints of the admin container."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.admin.deps import PrincipalDep, get_services

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe used by the compose healthcheck.

    Returns:
        Always-ok payload; it says nothing about dependencies.
    """
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    request: Request,
    principal: PrincipalDep,
) -> JSONResponse:
    """Readiness of every dependency the panel reads from.

    Args:
        request: Incoming request.
        principal: Authenticated operator.

    Returns:
        Per-dependency booleans with 200/503.
    """
    services = get_services(request)
    result = await services.get_deps()
    if not result.ok:
        return JSONResponse(
            status_code=503,
            content={"ready": False, "error": result.error},
        )
    statuses = {item.name: item.ok for item in result.data}
    ready = all(statuses.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, **statuses},
    )
