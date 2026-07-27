from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsSchema(BaseSettings):
    """Схема настроек приложения"""

    # Логирование
    LOG_LEVEL: str = Field(default="DEBUG", description="Level of logging")

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
