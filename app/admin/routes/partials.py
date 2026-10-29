"""HTMX fragments polled by the pages."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.admin.collectors.logs import LOG_NAME_RE
from app.admin.deps import PrincipalDep, get_services, render_template
from app.admin.routes import ERROR_LEVELS, LOG_LEVELS, normalize_level

router = APIRouter()


@router.get("/partials/overview", response_class=HTMLResponse)
async def overview(
    request: Request,
    principal: PrincipalDep,
) -> HTMLResponse:
    """Runtime, dependency and counter cards.

    Args:
        request: Incoming request.
        principal: Authenticated operator.

    Returns:
        Rendered overview fragment.
    """
    services = get_services(request)
    (
        deps,
        runtime,
        queues,
        errors,
        postgres,
        redis_stats,
        qdrant,
    ) = await asyncio.gather(
        services.get_deps(),
        services.get_runtime(),
        services.get_queues(),
        services.get_errors(limit=5),
        services.get_postgres_stats(),
        services.get_redis_stats(),
        services.get_qdrant_stats(),
    )
    return render_template(
        request,
        "partials/overview.html",
        {
            "deps": deps,
            "runtime": runtime,
            "queues": queues,
            "errors": errors,
            "postgres": postgres,
            "redis_stats": redis_stats,
            "qdrant": qdrant,
        },
    )


@router.get("/partials/containers", response_class=HTMLResponse)
async def containers_table(
    request: Request,
    principal: PrincipalDep,
) -> HTMLResponse:
    """Container table fragment.

    Args:
        request: Incoming request.
        principal: Authenticated operator.

    Returns:
        Rendered container table.
    """
    services = get_services(request)
    containers = await services.get_containers()
    return render_template(
        request,
        "partials/containers_table.html",
        {"containers": containers},
    )


@router.get("/partials/container/{service}", response_class=HTMLResponse)
async def container_status(
    request: Request,
    service: str,
    principal: PrincipalDep,
) -> HTMLResponse:
    """Status card fragment of one container.

    Args:
        request: Incoming request.
        service: Compose service name.
        principal: Authenticated operator.

    Returns:
        Rendered status card.
    """
    services = get_services(request)
    container = await services.find_container(service)
    return render_template(
        request,
        "partials/container_status.html",
        {"container": container},
        status_code=200 if container is not None else 404,
    )


@router.get("/partials/container/{service}/logs", response_class=HTMLResponse)
async def container_logs(
    request: Request,
    service: str,
    principal: PrincipalDep,
) -> HTMLResponse:
    """Log tail fragment of one container.

    Args:
        request: Incoming request.
        service: Compose service name.
        principal: Authenticated operator.

    Returns:
        Rendered log lines.
    """
    services = get_services(request)
    container = await services.find_container(service)
    if container is None:
        return render_template(
            request,
            "partials/container_logs.html",
            {"container": None, "logs": None},
            status_code=404,
        )
    logs = await services.get_container_logs(container, tail=200)
    return render_template(
        request,
        "partials/container_logs.html",
        {"container": container, "logs": logs},
    )


@router.get("/partials/queues", response_class=HTMLResponse)
async def queues_table(
    request: Request,
    principal: PrincipalDep,
) -> HTMLResponse:
    """Queue table fragment.

    Args:
        request: Incoming request.
        principal: Authenticated operator.

    Returns:
        Rendered queue table.
    """
    services = get_services(request)
    queues = await services.get_queues()
    return render_template(
        request,
        "partials/queues_table.html",
        {"queues": queues},
    )


@router.get("/partials/errors", response_class=HTMLResponse)
async def errors_table(
    request: Request,
    principal: PrincipalDep,
    level: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """Error table fragment with an optional level filter.

    Args:
        request: Incoming request.
        level: Optional ``ERROR``/``CRITICAL`` filter.
        principal: Authenticated operator.

    Returns:
        Rendered error table.
    """
    services = get_services(request)
    level_filter = normalize_level(level, ERROR_LEVELS)
    errors = await services.get_errors(limit=50, level=level_filter)
    return render_template(
        request,
        "partials/errors_table.html",
        {"errors": errors, "level": level_filter or ""},
    )


@router.get("/partials/logs", response_class=HTMLResponse)
async def logs_lines(
    request: Request,
    principal: PrincipalDep,
    file: Annotated[str, Query()] = "",
    level: Annotated[str, Query()] = "",
    q: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """Log line fragment for the polling container.

    Args:
        request: Incoming request.
        file: Whitelisted log file name.
        level: Optional exact level filter.
        q: Optional case-insensitive substring.
        principal: Authenticated operator.

    Returns:
        Rendered log lines.

    Raises:
        HTTPException: The file name fails the whitelist (404).
    """
    if not LOG_NAME_RE.match(file):
        raise HTTPException(status_code=404, detail="Unknown log file")
    services = get_services(request)
    level_filter = normalize_level(level, LOG_LEVELS)
    query = q.strip()[:200]
    logs = await services.get_logs(file, level_filter, query or None, 200)
    return render_template(
        request,
        "partials/logs_lines.html",
        {"logs": logs, "level": level_filter or "", "query": query},
    )
