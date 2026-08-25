"""API Gateway: auth, health, messaging-издатель, зависимости.

Роутеры каналов лежат в app/chats/* и подключаются в main напрямую —
здесь их не импортируем (защита от циклических импортов).
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
