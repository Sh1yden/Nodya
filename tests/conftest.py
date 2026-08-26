"""Root fixtures for the Nodya test suite."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest


@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure every test has required env vars set."""
    defaults: dict[str, str] = {
        "LOG_LEVEL": "DEBUG",
        "APP_PORT": "8014",
        "SYSTEM_SKILLS_ENABLED": "false",
        "SANDBOX_ENABLED": "true",
        "OWNER_USERNAME": "TestOwner",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_ASYNCPG": "asyncpg",
        "POSTGRES_DB": "nodya_test",
        "POSTGRES_USER": "test",
        "POSTGRES_PASSWORD": "test",
        "POSTGRES_PORT": "5432",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "RABBITMQ_HOST": "localhost",
        "RABBITMQ_PORT": "5672",
        "RABBITMQ_USER": "guest",
        "RABBITMQ_PASSWORD": "guest",
        "RABBITMQ_VHOST": "/",
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "QDRANT_COLLECTION": "nodya_test",
        "CONSOLIDATION_SCAN_MINUTES": "30",
        "CONSOLIDATION_IDLE_MINUTES": "180",
        "CONSOLIDATION_MIN_MESSAGES": "8",
        "FACTS_IN_PROMPT_LIMIT": "20",
        "VECTOR_SEARCH_LIMIT": "5",
        "FACTS_MIN_CONFIDENCE": "0.4",
        "GEMINI_API_KEY": "test-gemini-key",
        "OPENROUTER_API_KEY": "test-openrouter-key",
        "LLM_DIALOGUE_GEMINI": "gemini-flash",
        "LLM_CS_GEMINI": "gemini-flash",
        "LLM_BP_OPENROUTER": "gemma-free",
        "LLM_FALLBACK_OPENROUTER": "nemotron-free",
        "LLM_EMBED_MODEL": "gemini-embedding",
        "LLM_HISTORY_LIMIT": "20",
        "TELEGRAM_BOT_TOKEN": "1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ",
        "TELEGRAM_WEBHOOK_URL": "https://example.com",
        "TELEGRAM_WEBHOOK_SECRET": "test-secret-token",
        "TUNNEL_TIMEOUT": "30",
        "DEBOUNCE_SECONDS": "5",
        "SCHEDULED_POLL_SECONDS": "30",
        "MAX_SCHEDULED_RETRIES": "5",
    }
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)


@pytest.fixture()
def settings() -> Any:
    """Return a fresh ``SettingsSchema``."""
    from app.core.config import SettingsSchema

    return SettingsSchema()


@pytest.fixture()
def mock_redis() -> AsyncMock:
    """AsyncMock with the public interface of ``RedisClient``."""
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    redis.close = AsyncMock()
    redis.issue_link_code = AsyncMock(return_value="ABCD1234")
    redis.consume_link_code = AsyncMock(return_value=uuid.uuid4())
    redis.rename_user_keys = AsyncMock()
    redis.get_state = AsyncMock(return_value=None)
    redis.set_state = AsyncMock()
    redis.get_state_field = AsyncMock(return_value=None)
    redis.push_context_many = AsyncMock()
    redis.push_context = AsyncMock()
    redis.get_context = AsyncMock(return_value=[])
    redis.replace_context = AsyncMock()
    redis.clear_context = AsyncMock()
    redis.context_length = AsyncMock(return_value=0)
    redis.acquire_lock = AsyncMock(return_value="lock-token-abc")
    redis.release_lock = AsyncMock(return_value=True)
    redis.is_locked = AsyncMock(return_value=False)
    redis.push_debounce = AsyncMock(return_value=1)
    redis.pop_debounce_batch = AsyncMock(return_value=[])
    redis.set_agent_online = AsyncMock()
    redis.is_agent_online = AsyncMock(return_value=False)
    redis.push_scheduled = AsyncMock()
    redis.pop_due_scheduled = AsyncMock(return_value=[])
    return redis


@pytest.fixture()
def mock_session() -> AsyncMock:
    """AsyncMock simulating ``sqlalchemy.ext.asyncio.AsyncSession``."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.merge = AsyncMock()
    return session


@pytest.fixture()
def mock_session_factory(
    mock_session: AsyncMock,
) -> Any:
    """Callable that returns ``mock_session`` as an async context manager."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory() -> Any:
        yield mock_session

    return _factory


@pytest.fixture()
def mock_publisher() -> AsyncMock:
    """AsyncMock with the public interface of ``MessagePublisher``."""
    pub = AsyncMock()
    pub.connect = AsyncMock()
    pub.close = AsyncMock()
    pub.publish_incoming = AsyncMock()
    pub.publish_outgoing = AsyncMock()
    pub.publish_dead_letter = AsyncMock()
    pub.is_connected = True
    return pub


@pytest.fixture()
def mock_llm_router() -> AsyncMock:
    """AsyncMock with the public interface of ``LLMRouter``."""
    from app.brain.llm_choice.base import LLMResponse

    router = AsyncMock()
    router.generate_with_fallback = AsyncMock(
        return_value=LLMResponse(text="Hello from LLM")
    )
    router.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    router.close = AsyncMock()
    return router


@pytest.fixture()
def mock_vectors() -> AsyncMock:
    """AsyncMock with the public interface of ``VectorMemory``."""
    vec = AsyncMock()
    vec.upsert_points = AsyncMock()
    vec.search = AsyncMock(return_value=[])
    vec.reassign_user = AsyncMock(return_value=0)
    vec.close = AsyncMock()
    return vec


@pytest.fixture()
def mock_aiogram_bot() -> AsyncMock:
    """AsyncMock wrapping ``aiogram.Bot``."""
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.session.close = AsyncMock()
    bot.delete_webhook = AsyncMock()
    bot.set_webhook = AsyncMock()
    info = Mock()
    info.url = "https://example.com/webhook/telegram"
    info.pending_update_count = 0
    bot.get_webhook_info = AsyncMock(return_value=info)
    return bot


@pytest.fixture()
def amqp_message_factory() -> Any:
    """Factory producing mock AMQP messages."""

    def _make(body: bytes | str | dict | Any) -> Mock:
        if isinstance(body, str):
            body = body.encode()
        elif isinstance(body, dict):
            import json

            body = json.dumps(body).encode()
        msg = Mock()
        msg.body = body
        msg.ack = AsyncMock()
        msg.nack = AsyncMock()
        msg.reject = AsyncMock()
        msg.is_delivered = False
        return msg

    return _make
