"""Server-rendered pages of the admin panel."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.admin.collectors.logs import LOG_NAME_RE
from app.admin.deps import PrincipalDep, get_services, render_template
from app.admin.routes import ERROR_LEVELS, LOG_LEVELS, normalize_level

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    principal: PrincipalDep,
) -> HTMLResponse:
    """Overview page with runtime, dependencies and counters.

    Args:
        request: Incoming request.
        principal: Authenticated operator.

    Returns:
        Rendered dashboard.
    """
    services = get_services(request)
    (
        deps,
        runtime,
        containers,
        queues,
        errors,
        postgres,
        redis_stats,
        qdrant,
    ) = await asyncio.gather(
        services.get_deps(),
        services.get_runtime(),
        services.get_containers(),
        services.get_queues(),
        services.get_errors(limit=5),
        services.get_postgres_stats(),
        services.get_redis_stats(),
        services.get_qdrant_stats(),
    )
    return render_template(
        request,
        "dashboard.html",
        {
            "principal": principal,
            "active": "dashboard",
            "deps": deps,
            "runtime": runtime,
            "containers": containers,
            "queues": queues,
            "errors": errors,
            "postgres": postgres,
            "redis_stats": redis_stats,
            "qdrant": qdrant,
        },
    )


@router.get("/containers", response_class=HTMLResponse)
async def containers_page(
    request: Request,
    principal: PrincipalDep,
) -> HTMLResponse:
    """All compose containers with state and health.

    Args:
        request: Incoming request.
        principal: Authenticated operator.

    Returns:
        Rendered container list.
    """
    services = get_services(request)
    containers = await services.get_containers()
    return render_template(
        request,
        "containers.html",
        {
            "principal": principal,
            "active": "containers",
            "containers": containers,
        },
    )


@router.get("/containers/{service}", response_class=HTMLResponse)
async def container_detail(
    request: Request,
    service: str,
    principal: PrincipalDep,
) -> HTMLResponse:
    """State and log tail of a single container.

    Args:
        request: Incoming request.
        service: Compose service name.
        principal: Authenticated operator.

    Returns:
        Rendered detail page or a 404 page.
    """
    services = get_services(request)
    container = await services.find_container(service)
    if container is None:
        containers = await services.get_containers()
        return render_template(
            request,
            "not_found.html",
            {
                "principal": principal,
                "active": "containers",
                "message": "Контейнер не найден или Docker недоступен.",
                "detail": containers.error,
            },
            status_code=404,
        )
    logs = await services.get_container_logs(container, tail=200)
    return render_template(
        request,
        "container_detail.html",
        {
            "principal": principal,
            "active": "containers",
            "container": container,
            "logs": logs,
        },
    )


@router.get("/queues", response_class=HTMLResponse)
async def queues_page(
    request: Request,
    principal: PrincipalDep,
) -> HTMLResponse:
    """Queue depths and consumer counts.

    Args:
        request: Incoming request.
        principal: Authenticated operator.

    Returns:
        Rendered queue page.
    """
    services = get_services(request)
    queues = await services.get_queues()
    return render_template(
        request,
        "queues.html",
        {
            "principal": principal,
            "active": "queues",
            "queues": queues,
        },
    )


@router.get("/errors", response_class=HTMLResponse)
async def errors_page(
    request: Request,
    principal: PrincipalDep,
    level: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """Grouped errors with an optional level filter.

    Args:
        request: Incoming request.
        level: Optional ``ERROR``/``CRITICAL`` filter.
        principal: Authenticated operator.

    Returns:
        Rendered errors page.
    """
    services = get_services(request)
    level_filter = normalize_level(level, ERROR_LEVELS)
    errors = await services.get_errors(limit=50, level=level_filter)
    return render_template(
        request,
        "errors.html",
        {
            "principal": principal,
            "active": "errors",
            "errors": errors,
            "level": level_filter or "",
            "levels": sorted(ERROR_LEVELS),
        },
    )


@router.get("/errors/{fingerprint}", response_class=HTMLResponse)
async def error_detail(
    request: Request,
    fingerprint: str,
    principal: PrincipalDep,
) -> HTMLResponse:
    """One error group with its traceback.

    Args:
        request: Incoming request.
        fingerprint: 64-character hex group id.
        principal: Authenticated operator.

    Returns:
        Rendered detail page, a 404 page or a 503 page.
    """
    services = get_services(request)
    result = await services.get_error(fingerprint)
    if not result.ok:
        return render_template(
            request,
            "not_found.html",
            {
                "principal": principal,
                "active": "errors",
                "message": "Ошибка не найдена.",
                "detail": result.error,
            },
            status_code=404,
        )
    if result.data is None:
        return render_template(
            request,
            "not_found.html",
            {
                "principal": principal,
                "active": "errors",
                "message": "Группа ошибок не найдена.",
            },
            status_code=404,
        )
    return render_template(
        request,
        "error_detail.html",
        {
            "principal": principal,
            "active": "errors",
            "error": result.data,
        },
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    principal: PrincipalDep,
    file: Annotated[str, Query()] = "",
    level: Annotated[str, Query()] = "",
    q: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """Tail JSONL application logs with level/substring filters.

    Args:
        request: Incoming request.
        file: Log file name; defaults to the newest file.
        level: Optional exact level filter.
        q: Optional case-insensitive substring.
        principal: Authenticated operator.

    Returns:
        Rendered logs page or a 404 page for a foreign file name.
    """
    services = get_services(request)
    if file and not LOG_NAME_RE.match(file):
        return render_template(
            request,
            "not_found.html",
            {
                "principal": principal,
                "active": "logs",
                "message": "Недопустимое имя файла лога.",
            },
            status_code=404,
        )
    files = await services.get_log_files()
    file_infos = files.data if files.ok else []
    chosen = file or (file_infos[0].name if file_infos else "")
    level_filter = normalize_level(level, LOG_LEVELS)
    query = q.strip()[:200]
    logs = None
    if chosen:
        logs = await services.get_logs(
            chosen, level_filter, query or None, 200
        )
    return render_template(
        request,
        "logs.html",
        {
            "principal": principal,
            "active": "logs",
            "files": files,
            "file_infos": file_infos,
            "chosen": chosen,
            "level": level_filter or "",
            "levels": sorted(LOG_LEVELS),
            "query": query,
            "logs": logs,
        },
    )
