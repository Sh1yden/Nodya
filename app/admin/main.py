"""Admin panel application, served from a dedicated container.

Run with:
    uvicorn app.admin.main:app --host 0.0.0.0 --port 8016
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.admin import formatting
from app.admin.deps import NotAuthenticated
from app.admin.routes import auth, health, pages, partials
from app.admin.services import AdminServices
from app.brain.memory.long import AsyncSessionLocal, engine
from app.brain.memory.short.redis import RedisClient
from app.core import get_logger, settings
from app.core.logger_config import setup_logging

logger = get_logger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
_VERSION = os.environ.get("NODYA_VERSION", "dev")
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _allowed_hosts() -> list[str]:
    """Parse the Host allowlist from the settings string.

    Returns:
        Non-empty host names for TrustedHostMiddleware.
    """
    return [
        item.strip()
        for item in settings.ADMIN_ALLOWED_HOSTS.split(",")
        if item.strip()
    ]


def create_app() -> FastAPI:
    """Build the admin FastAPI application.

    The app never fails fast and never starts workers: it must come
    up even when PostgreSQL, Redis, RabbitMQ or Qdrant are down.

    Returns:
        Configured FastAPI instance.
    """
    setup_logging(level=settings.LOG_LEVEL, file=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis_client = RedisClient(settings.redis_url)
        services = AdminServices(
            settings=settings,
            redis=redis_client,
            session_factory=AsyncSessionLocal,
        )
        app.state.admin = services
        logger.info("Admin panel started")
        try:
            yield
        finally:
            await services.close()
            await redis_client.close()
            await engine.dispose()
            logger.info("Admin panel stopped")

    app = FastAPI(
        title="Nodya Admin",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))
    templates.env.filters.update(
        dt=formatting.format_datetime,
        iso=formatting.format_iso,
        uptime=formatting.format_uptime,
        ago=formatting.format_ago,
        bytes=formatting.format_bytes,
        status_class=formatting.status_class,
        container_class=formatting.container_class,
        level_class=formatting.level_class,
    )
    templates.env.globals.update(
        version=_VERSION,
        grafana_url=settings.GRAFANA_PUBLIC_URL,
        dep_labels={
            "postgres": "PostgreSQL",
            "rabbitmq": "RabbitMQ",
            "redis": "Redis",
            "qdrant": "Qdrant",
            "docker": "Docker",
        },
    )
    app.state.templates = templates
    app.state.settings = settings

    app.mount(
        "/static",
        StaticFiles(directory=str(_BASE_DIR / "static")),
        name="static",
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts())

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(NotAuthenticated)
    async def unauthenticated(
        request: Request, exc: NotAuthenticated
    ) -> Response:
        target = quote(request.url.path, safe="/")
        login_url = f"/login?next={target}"
        if request.url.path.startswith("/partials/"):
            return Response(
                status_code=401,
                headers={"HX-Redirect": login_url},
            )
        return RedirectResponse(login_url, status_code=303)

    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(partials.router)
    app.include_router(health.router)
    return app


app = create_app()
