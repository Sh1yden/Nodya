"""Точка входа Nodya: API Gateway + фоновые задачи в одном процессе.

Поток среза C:
Telegram -> POST /webhook/telegram -> RabbitMQ(incoming) -> Worker(эхо)
-> RabbitMQ(outgoing) -> TGSender -> Telegram.
"""

import asyncio
from contextlib import asynccontextmanager, suppress

from aiogram import Bot
from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.health import (
    ping_postgres,
    ping_qdrant,
    ping_rabbitmq,
)
from app.api.health import (
    router as health_router,
)
from app.api.messaging import MessagePublisher
from app.api.tunnels import start_tunnel, stop_tunnel
from app.api.webhook_tg import router as webhook_router
from app.brain.memory.long.database import AsyncSessionLocal, engine
from app.brain.memory.short.redis import RedisClient
from app.core import get_logger, settings, setup_logging
from app.senders.tg_sender import TGSender
from app.worker import Worker

setup_logging(level=settings.LOG_LEVEL)
_lg = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap (fail-fast) и graceful shutdown всех подсистем."""
    await _fail_fast()

    publisher = MessagePublisher(settings.rabbitmq_url)
    await publisher.connect()
    redis_client = RedisClient(settings.redis_url)

    worker = Worker(
        broker_url=settings.rabbitmq_url,
        redis_client=redis_client,
        session_factory=AsyncSessionLocal,
        publisher=publisher,
    )
    sender = TGSender(
        broker_url=settings.rabbitmq_url,
        bot_token=settings.TELEGRAM_BOT_TOKEN,
    )
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    tunnel_process = None
    base_url = settings.TELEGRAM_WEBHOOK_URL.strip()
    ephemeral_webhook = not base_url
    if ephemeral_webhook:
        _lg.info("WEBHOOK_URL пуст — поднимаю туннель.")
        base_url, tunnel_process = await asyncio.to_thread(
            start_tunnel, settings.APP_PORT
        )

    await _set_webhook(bot, base_url)

    background_tasks = [
        asyncio.create_task(worker.run(), name="worker"),
        asyncio.create_task(sender.run(), name="tg-sender"),
    ]
    app.state.publisher = publisher
    app.state.redis = redis_client
    _lg.info("Nodya запущена. Вебхук: %s/webhook/telegram", base_url)

    yield

    _lg.info("Остановка Nodya...")
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    await worker.stop()
    await sender.stop()

    if ephemeral_webhook:
        with suppress(Exception):
            await bot.delete_webhook(drop_pending_updates=False)
    await bot.session.close()
    await publisher.close()
    await redis_client.close()
    await engine.dispose()
    if tunnel_process is not None:
        await asyncio.to_thread(stop_tunnel, tunnel_process)


async def _fail_fast() -> None:
    """Упасть при старте, если какая-то зависимость недоступна."""
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN пуст.")
    redis_probe = RedisClient(settings.redis_url)
    try:
        checks = {
            "postgres": ping_postgres(),
            "redis": redis_probe.ping(),
            "rabbitmq": ping_rabbitmq(),
            "qdrant": ping_qdrant(),
        }
        results = await asyncio.gather(*checks.values())
    finally:
        await redis_probe.close()
    failed = [name for name, ok in zip(checks, results, strict=True) if not ok]
    if failed:
        raise RuntimeError(f"Bootstrap failed, недоступны: {failed}")


async def _set_webhook(bot: Bot, base_url: str) -> None:
    """delete старого URL + set нового с секретом (поправка 5).

    drop_pending при delete=True — чистим хвост мёртвого прошлого
    туннеля; на новом set pending=False: офлайн-сообщения простоя
    доставляются после старта, а не выбрасываются.
    """
    url = f"{base_url.rstrip('/')}/webhook/telegram"
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(
        url=url,
        secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
        allowed_updates=["message"],
        drop_pending_updates=False,
    )
    info = await bot.get_webhook_info()
    _lg.info("Вебхук активен: %s", info.url)
    if info.pending_update_count:
        _lg.warning(
            "В очереди Telegram %d офлайн-сообщений — будут "
            "обработаны по мере доставки.",
            info.pending_update_count,
        )


app = FastAPI(title="Nodya", lifespan=lifespan)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(webhook_router)
