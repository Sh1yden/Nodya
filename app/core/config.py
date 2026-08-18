from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsSchema(BaseSettings):
    """Схема настроек приложения"""

    # Логирование
    LOG_LEVEL: str = Field(default="DEBUG")

    # Sys skills
    SYSTEM_SKILLS_ENABLED: bool = Field(
        default=False, description="Skills enabled in system of run"
    )
    SANDBOX_ENABLED: bool = Field(
        default=True, description="Sandbox mode(in docker container) for skills."
    )

    # Owner sys
    OWNER_USERNAME: str = Field(default="Shayden", description="Admin username.")

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

    # Qdrant
    QDRANT_HOST: str = Field(default="localhost")
    QDRANT_PORT: int = Field(default=6333)

    # AI API'S
    GEMINI_API_KEY: str
    OPENROUTER_API_KEY: str

    # Chats tokens
    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_WEBHOOK_URL: str

    @computed_field
    @property
    def rabbitmq_url(self) -> str:
        return (
            f"ampq://"
            f"{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@"
            f"{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/virtual_host"
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
