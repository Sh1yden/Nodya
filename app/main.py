"""Entry point of Nodya: API Gateway + background tasks in one process.

Flow: Telegram -> webhook (app.chats.telegram) -> RabbitMQ(incoming)
-> Worker -> RabbitMQ(outgoing) -> TGSender -> Telegram.
"""

import asyncio
from contextlib import asynccontextmanager, suppress

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.api import (
    MessagePublisher,
    auth_router,
    health_router,
    ping_postgres,
    ping_qdrant,
    ping_rabbitmq,
)
from app.api.tunnels import start_tunnel, stop_tunnel
from app.brain.llm_choice import (
    GeminiCloudflareProvider,
    GeminiProvider,
    LLMRouter,
    OpenRouterProvider,
    ProviderRegistry,
)
from app.brain.memory.consolidation import ConsolidationJob
from app.brain.memory.long import AsyncSessionLocal, engine
from app.brain.memory.short import RedisClient
from app.brain.memory.vector import VectorMemory
from app.chats.telegram import TGSender
from app.chats.telegram import router as telegram_webhook_router
from app.core import get_logger, settings, setup_logging
from app.worker import Worker

setup_logging(level=settings.LOG_LEVEL)
_lg = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap with fail-fast, then graceful shutdown of everything.

    Args:
        app: FastAPI application instance being started/stopped.

    Yields:
        None once the application is fully running.
    """
    await _fail_fast()

    registry = ProviderRegistry(settings)
    registry.register(
        "gemini_cloudflare", GeminiCloudflareProvider, enabled=True
    )
    registry.register("openrouter", OpenRouterProvider, enabled=True)
    if settings.GEMINI_ENABLED:
        registry.register("gemini", GeminiProvider, enabled=True)

    router = LLMRouter(registry=registry)
    publisher = MessagePublisher(settings.rabbitmq_url)
    await publisher.connect()
    redis_client = RedisClient(settings.redis_url)
    vectors = VectorMemory(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        collection=settings.QDRANT_COLLECTION,
    )

    worker = Worker(
        broker_url=settings.rabbitmq_url,
        redis_client=redis_client,
        session_factory=AsyncSessionLocal,
        publisher=publisher,
        router=router,
        vectors=vectors,
    )
    consolidation = ConsolidationJob(
        redis_client=redis_client,
        session_factory=AsyncSessionLocal,
        router=router,
        vectors=vectors,
    )
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        consolidation.run_for_all,
        "interval",
        minutes=settings.CONSOLIDATION_SCAN_MINUTES,
        id="consolidation-scan",
        max_instances=1,
        coalesce=True,
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
        _lg.debug("No TELEGRAM_WEBHOOK_URL — starting cloudflared tunnel.")
        base_url, tunnel_process = await asyncio.to_thread(
            start_tunnel, settings.APP_PORT
        )

    await _set_webhook(bot, base_url)

    background_tasks = [
        asyncio.create_task(worker.run(), name="worker"),
        asyncio.create_task(sender.run(), name="tg-sender"),
    ]
    scheduler.start()
    app.state.publisher = publisher
    app.state.redis = redis_client
    _lg.info(f"Nodya is up. Webhook: {base_url}/webhook/telegram")

    yield

    _lg.info("Stopping Nodya...")
    scheduler.shutdown(wait=False)
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    await worker.stop()
    await sender.stop()

    if ephemeral_webhook:
        # Ephemeral tunnel dies anyway; clear the stale URL at Telegram.
        with suppress(Exception):
            await bot.delete_webhook(drop_pending_updates=False)
    await bot.session.close()
    await publisher.close()
    await router.close()
    await redis_client.close()
    await vectors.close()
    await engine.dispose()
    if tunnel_process is not None:
        await asyncio.to_thread(stop_tunnel, tunnel_process)


async def _fail_fast() -> None:
    """Abort startup when a dependency or credential is unavailable.

    Raises:
        RuntimeError: Empty token/API keys, or any infrastructure
            service failing its ping.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        _lg.critical("TELEGRAM_BOT_TOKEN is empty.")
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty.")
    if not settings.GEMINI_API_KEY:
        _lg.critical("GEMINI_API_KEY is empty.")
        raise RuntimeError("GEMINI_API_KEY is empty.")
    if not settings.OPENROUTER_API_KEY:
        _lg.critical("OPENROUTER_API_KEY is empty.")
        raise RuntimeError("OPENROUTER_API_KEY is empty.")
    uses_gemini_cf = any(
        item.get("provider") == "gemini_cloudflare"
        for chain in settings.LLM_PROVIDER_CHAINS.values()
        for item in chain
    )
    if uses_gemini_cf and not settings.GEMINI_CLOUDFLARE_URL:
        _lg.critical("GEMINI_CLOUDFLARE_URL is empty but required.")
        raise RuntimeError("GEMINI_CLOUDFLARE_URL is empty but required.")

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
        _lg.critical(f"Bootstrap failed, unreachable: {failed}")
        raise RuntimeError(f"Bootstrap failed, unreachable: {failed}")


async def _set_webhook(bot: Bot, base_url: str) -> None:
    """Delete the old webhook and set the new one with the secret.

    drop_pending=True on delete clears leftovers of a dead previous
    tunnel; the new set uses pending=False so offline messages of the
    downtime get delivered instead of being discarded.

    Args:
        bot: aiogram Bot instance used for Telegram API calls.
        base_url: Public base URL hosting /webhook/telegram.
    """
    url = f"{base_url.rstrip('/')}/webhook/telegram"
    await bot.delete_webhook(drop_pending_updates=True)
    _lg.debug("Old webhook deleted.")
    await bot.set_webhook(
        url=url,
        secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
        allowed_updates=["message"],
        drop_pending_updates=False,
    )
    info = await bot.get_webhook_info()
    _lg.info(f"Webhook active: {info.url}")
    if info.pending_update_count:
        _lg.warning(
            f"{info.pending_update_count} offline messages queued in "
            "Telegram — they will be processed on delivery."
        )


app = FastAPI(title="Nodya", lifespan=lifespan)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(telegram_webhook_router)
