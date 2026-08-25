"""API Gateway: auth, health, messaging publisher, dependencies.

Channel routers live in app/chats/* and are wired in main directly —
they are intentionally not imported here (import-cycle protection).
"""

from .auth import router as auth_router
from .deps import get_current_user
from .health import ping_postgres, ping_qdrant, ping_rabbitmq
from .health import router as health_router
from .messaging import MessagePublisher

__all__ = [
    "MessagePublisher",
    "auth_router",
    "get_current_user",
    "health_router",
    "ping_postgres",
    "ping_qdrant",
    "ping_rabbitmq",
]
