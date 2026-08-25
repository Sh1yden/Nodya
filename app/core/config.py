from urllib.parse import quote

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsSchema(BaseSettings):
    """Схема настроек приложения"""

    # Логирование
    LOG_LEVEL: str = Field(default="DEBUG")

    # HTTP
    APP_PORT: int = Field(default=8014)

    # Sys skills
    SYSTEM_SKILLS_ENABLED: bool = Field(
        default=False, description="Skills enabled in system of run"
    )
    SANDBOX_ENABLED: bool = Field(
        default=True,
        description="Sandbox mode(in docker container) for skills.",
    )

    # Owner sys
    OWNER_USERNAME: str = Field(
        default="Shayden", description="Admin username."
    )

    # PostgreSQL
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_ASYNCPG: str = Field(default="asyncpg")
    POSTGRES_DB: str = Field(default="postgres")
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    POSTGRES_PORT: int = Field(default=5434)

    # Redis
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6381)

    # RabbitMQ
    RABBITMQ_HOST: str = Field(default="localhost")
    RABBITMQ_PORT: int = Field(default=5672)
    RABBITMQ_USER: str = Field(default="guest")
    RABBITMQ_PASSWORD: str = Field(default="guest")
    RABBITMQ_VHOST: str = Field(default="/")

    # Qdrant
    QDRANT_HOST: str = Field(default="localhost")
    QDRANT_PORT: int = Field(default=6333)

    # AI API'S (строгая проверка при инициализации провайдеров в main)
    GEMINI_API_KEY: str = Field(default="")
    OPENROUTER_API_KEY: str = Field(default="")

    # LLM-роли (матрица из tldr: D/CS/BP/VS).
    # Цепочки — порядок кандидатов через запятую; после Gemini-части
    # роутер добавляет OpenRouter-fallback (nemotron пара).
    LLM_DIALOGUE_GEMINI: str = Field(
        default="gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    )
    LLM_CS_GEMINI: str = Field(default="gemini-3.6-flash")
    LLM_BP_OPENROUTER: str = Field(
        default=("google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free")
    )
    LLM_FALLBACK_OPENROUTER: str = Field(
        default=(
            "nvidia/nemotron-3-ultra-550b-a55b:free,"
            "nvidia/nemotron-3-super-120b-a12b:free"
        )
    )
    LLM_EMBED_MODEL: str = Field(default="gemini-embedding-2")
    LLM_HISTORY_LIMIT: int = Field(default=20)

    # Chats tokens
    # Telegram
    TELEGRAM_BOT_TOKEN: str = Field(default="")
    TELEGRAM_WEBHOOK_URL: str = Field(
        default="",
        description=(
            "Публичный базовый URL вебхука. "
            "Пусто -> туннель cloudflared (только локальный запуск)."
        ),
    )
    TELEGRAM_WEBHOOK_SECRET: str = Field(
        description="Секрет заголовка X-Telegram-Bot-Api-Secret-Token."
    )

    # Туннель (локальная разработка)
    TUNNEL_TIMEOUT: int = Field(default=30)

    # Worker (поправки 1/2/4)
    DEBOUNCE_SECONDS: int = Field(default=5)
    SCHEDULED_POLL_SECONDS: int = Field(default=30)
    MAX_SCHEDULED_RETRIES: int = Field(default=5)

    @computed_field
    @property
    def rabbitmq_url(self) -> str:
        vhost = quote(self.RABBITMQ_VHOST, safe="")
        return (
            f"amqp://"
            f"{quote(self.RABBITMQ_USER)}:{quote(self.RABBITMQ_PASSWORD)}@"
            f"{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/{vhost}"
        )

    @computed_field
    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+{self.POSTGRES_ASYNCPG}://"
            f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field
    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
        case_sensitive=True,
    )


# Глобальный экземпляр настроек
settings = SettingsSchema()
