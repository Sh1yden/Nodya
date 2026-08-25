"""Health-check: parallel pings of every external dependency.

Used twice:
- fail-fast at bootstrap (lifespan calls the ping functions directly);
- GET /health at runtime — never crashes on partial failure.
"""

import asyncio
from typing import Any

import aio_pika
import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.brain.memory.long import AsyncSessionLocal
from app.core import get_logger, settings

logger = get_logger(__name__)

router = APIRouter()

_PING_TIMEOUT_SECONDS = 5.0


async def ping_postgres() -> bool:
    """Run SELECT 1 through the application connection pool."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning(f"PostgreSQL unreachable: {exc}")
        return False


async def ping_rabbitmq() -> bool:
    """Open a short-lived non-robust connection to the broker."""
    try:
        connection = await asyncio.wait_for(
            aio_pika.connect(settings.rabbitmq_url),
            timeout=_PING_TIMEOUT_SECONDS,
        )
        await connection.close()
        return True
    except Exception as exc:
        logger.warning(f"RabbitMQ unreachable: {exc}")
        return False


async def ping_qdrant() -> bool:
    """Probe the Qdrant HTTP /healthz endpoint."""
    url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}/healthz"
    try:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
        return response.status_code == 200
    except Exception as exc:
        logger.warning(f"Qdrant unreachable: {exc}")
        return False


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Report dependency status; 503 if any of them is down.

    Args:
        request: Incoming request (used to fetch app state).

    Returns:
        JSONResponse with per-service booleans and 200/503 code.
    """
    checks: dict[str, Any] = {
        "postgres": ping_postgres,
        "rabbitmq": ping_rabbitmq,
        "qdrant": ping_qdrant,
    }
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        checks["redis"] = redis_client.ping
    gathered = await asyncio.gather(*(check() for check in checks.values()))
    results = dict(zip(checks.keys(), map(bool, gathered), strict=True))
    healthy = all(results.values())
    if not healthy:
        down = [name for name, ok in results.items() if not ok]
        logger.warning(f"Health check failed for: {down}")
    code = (
        status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(
        status_code=code,
        content={"healthy": healthy, **results},
    )
