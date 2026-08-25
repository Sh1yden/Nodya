"""Health-check: параллельный пинг всех внешних сервисов.

Используется дважды:
- fail-fast при bootstrap (lifespan вызывает ping-функции напрямую);
- GET /health в рантайме — не роняет процесс при частичном отказе.
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
    """SELECT 1 через пул соединений приложения."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("PostgreSQL недоступен.")
        return False


async def ping_rabbitmq() -> bool:
    """Короткое неборастяжное соединение с брокером."""
    try:
        connection = await asyncio.wait_for(
            aio_pika.connect(settings.rabbitmq_url),
            timeout=_PING_TIMEOUT_SECONDS,
        )
        await connection.close()
        return True
    except Exception:
        logger.exception("RabbitMQ недоступен.")
        return False


async def ping_qdrant() -> bool:
    """HTTP /healthz Qdrant."""
    url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}/healthz"
    try:
        async with httpx.AsyncClient(timeout=_PING_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
        return response.status_code == 200
    except Exception:
        logger.exception("Qdrant недоступен.")
        return False


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Статус зависимостей; 503, если хотя бы одна недоступна."""
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
    code = (
        status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(
        status_code=code,
        content={"healthy": healthy, **results},
    )
