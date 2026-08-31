"""Typed application settings loaded from .env (pydantic-settings)."""

from urllib.parse import quote

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsSchema(BaseSettings):
    """All environment variables of the Nodya application."""

    # Logging
    LOG_LEVEL: str = Field(default="DEBUG")

    # HTTP
    APP_PORT: int = Field(default=8014)

    # System skills
    SYSTEM_SKILLS_ENABLED: bool = Field(
        default=False, description="Enable system-tier skills execution."
    )
    SANDBOX_ENABLED: bool = Field(
        default=True,
        description="Run sandboxed skills inside a Docker container.",
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
    QDRANT_COLLECTION: str = Field(default="nodya_memory")

    # Consolidation (sleep phase, stage 7.2 + D4 amendment)
    CONSOLIDATION_SCAN_MINUTES: int = Field(default=30)
    CONSOLIDATION_IDLE_MINUTES: int = Field(
        default=180,
        description="Silence required before a user is consolidated.",
    )
    CONSOLIDATION_MIN_MESSAGES: int = Field(default=8)
    FACTS_IN_PROMPT_LIMIT: int = Field(default=20)
    VECTOR_SEARCH_LIMIT: int = Field(default=5)
    FACTS_MIN_CONFIDENCE: float = Field(default=0.4)

    # AI APIs
    GEMINI_API_KEY: str = Field(default="")
    OPENROUTER_API_KEY: str = Field(default="")
    GEMINI_CLOUDFLARE_URL: str = Field(
        default="",
        description=(
            "Cloudflare Worker URL proxying Gemini API. "
            "Self-hosters must set their own worker; "
            "empty fails fast if gemini_cloudflare is in chain."
        ),
    )
    GEMINI_ENABLED: bool = Field(default=False)

    # Deprecated (kept for backward compat with existing .env, not used).
    OWNER_USERNAME: str | None = Field(
        default=None, description="Deprecated: owner via bootstrap CLI."
    )
    LLM_DIALOGUE_GEMINI: str | None = Field(default=None)
    LLM_CS_GEMINI: str | None = Field(default=None)
    LLM_BP_OPENROUTER: str | None = Field(default=None)
    LLM_FALLBACK_OPENROUTER: str | None = Field(default=None)
    LLM_EMBED_MODEL: str | None = Field(default=None)

    # LLM provider chains (priority fallback).
    # Each role maps to a list of {"provider": "...", "models": "..."}.
    # Provider names must match registry registrations; order is priority.
    LLM_PROVIDER_CHAINS: dict = Field(
        default={
            "dialogue": [
                {
                    "provider": "gemini_cloudflare",
                    "models": "gemini-3.5-flash-lite,gemini-3.1-flash-lite",
                },
                {
                    "provider": "openrouter",
                    "models": (
                        "nvidia/nemotron-3-ultra-550b-a55b:free,"
                        "nvidia/nemotron-3-super-120b-a12b:free"
                    ),
                },
                {
                    "provider": "openrouter",
                    "models": (
                        "anthropic/claude-3.5-haiku:free,"
                        "meta-llama/llama-3.1-8b-instruct:free"
                    ),
                },
            ],
            "cs": [
                {
                    "provider": "gemini_cloudflare",
                    "models": "gemini-3.6-flash",
                },
                {
                    "provider": "openrouter",
                    "models": (
                        "nvidia/nemotron-3-ultra-550b-a55b:free,"
                        "nvidia/nemotron-3-super-120b-a12b:free"
                    ),
                },
                {
                    "provider": "openrouter",
                    "models": "anthropic/claude-3.5-haiku:free",
                },
            ],
            "bp": [
                {
                    "provider": "openrouter",
                    "models": (
                        "google/gemma-4-31b-it:free,"
                        "google/gemma-4-26b-a4b-it:free"
                    ),
                },
                {
                    "provider": "openrouter",
                    "models": (
                        "nvidia/nemotron-3-ultra-550b-a55b:free,"
                        "nvidia/nemotron-3-super-120b-a12b:free"
                    ),
                },
                {
                    "provider": "openrouter",
                    "models": "qwen/qwen-2.5-coder-32b-instruct:free",
                },
            ],
            "vs": [
                {
                    "provider": "gemini_cloudflare",
                    "models": "gemini-embedding-2",
                },
            ],
        }
    )

    LLM_HISTORY_LIMIT: int = Field(default=20)

    # Telegram
    TELEGRAM_BOT_TOKEN: str = Field(default="")
    TELEGRAM_WEBHOOK_URL: str = Field(
        default="",
        description=(
            "Public base URL of the webhook. Empty -> cloudflared "
            "tunnel (local runs only)."
        ),
    )
    TELEGRAM_WEBHOOK_SECRET: str = Field(
        description="Secret for the X-Telegram-Bot-Api-Secret-Token header."
    )

    # Tunnel (local development)
    TUNNEL_TIMEOUT: int = Field(default=30)

    # Worker (amendments 1/2/4)
    DEBOUNCE_SECONDS: int = Field(default=5)
    SCHEDULED_POLL_SECONDS: int = Field(default=30)
    MAX_SCHEDULED_RETRIES: int = Field(default=5)

    @computed_field
    @property
    def rabbitmq_url(self) -> str:
        """AMQP URL with the percent-encoded vhost path."""
        vhost = quote(self.RABBITMQ_VHOST, safe="")
        return (
            f"amqp://"
            f"{quote(self.RABBITMQ_USER)}:{quote(self.RABBITMQ_PASSWORD)}@"
            f"{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/{vhost}"
        )

    @computed_field
    @property
    def postgres_url(self) -> str:
        """SQLAlchemy async DSN for the application database."""
        return (
            f"postgresql+{self.POSTGRES_ASYNCPG}://"
            f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @computed_field
    @property
    def redis_url(self) -> str:
        """Redis connection URL for short-term memory."""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=True,
    )


# Global settings instance
settings = SettingsSchema()
