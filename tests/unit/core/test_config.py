"""Unit tests for ``app.core.config``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import SettingsSchema


class TestSettingsSchema:
    """SettingsSchema validation and computed fields."""

    def test_defaults_when_env_patched(self, settings: object) -> None:
        assert settings.LOG_LEVEL == "DEBUG"  # type: ignore[attr-defined]
        assert settings.APP_PORT == 8014  # type: ignore[attr-defined]
        assert settings.OWNER_USERNAME == "TestOwner"  # type: ignore[attr-defined]

    def test_postgres_url_computed(self, settings: object) -> None:
        url: str = settings.postgres_url  # type: ignore[attr-defined]
        assert url.startswith("postgresql+asyncpg://")
        assert "nodya_test" in url

    def test_redis_url_computed(self, settings: object) -> None:
        url: str = settings.redis_url  # type: ignore[attr-defined]
        assert url.startswith("redis://")

    def test_rabbitmq_url_computed(self, settings: object) -> None:
        url: str = settings.rabbitmq_url  # type: ignore[attr-defined]
        assert url.startswith("amqp://")
        assert "guest" in url

    def test_numeric_fields_are_int(self, settings: object) -> None:
        assert isinstance(settings.POSTGRES_PORT, int)  # type: ignore[attr-defined]
        assert isinstance(settings.REDIS_PORT, int)  # type: ignore[attr-defined]

    def test_float_field(self, settings: object) -> None:
        assert isinstance(
            settings.FACTS_MIN_CONFIDENCE,
            float,  # type: ignore[attr-defined]
        )
        assert (
            pytest.approx(  # type: ignore[attr-defined]
                0.4
            )
            == settings.FACTS_MIN_CONFIDENCE
        )

    def test_telegram_secret_has_no_default(self) -> None:
        """TELEGRAM_WEBHOOK_SECRET must be provided via env."""
        field = SettingsSchema.model_fields["TELEGRAM_WEBHOOK_SECRET"]
        assert field.is_required()

    def test_default_media_chain_exists(self, settings: object) -> None:
        chains: dict = settings.LLM_PROVIDER_CHAINS  # type: ignore[attr-defined]
        assert "media" in chains
        providers = [item["provider"] for item in chains["media"]]
        assert providers[0] == "gemini_cloudflare"
        assert "openrouter" not in providers

    def test_media_limits_defaults(self, settings: object) -> None:
        assert (
            settings.MEDIA_IMAGE_MAX_BYTES  # type: ignore[attr-defined]
            == 5 * 1024 * 1024
        )
        assert (
            settings.MEDIA_DESCRIPTION_MAX_CHARS  # type: ignore[attr-defined]
            == 600
        )
        assert settings.MEDIA_MAX_PER_BATCH == 10  # type: ignore[attr-defined]

    def test_logging_defaults(self, settings: object) -> None:
        assert settings.LOG_DIR == "logs"  # type: ignore[attr-defined]
        assert settings.LOG_RETENTION_DAYS == 14  # type: ignore[attr-defined]

    def test_admin_defaults(self, settings: object) -> None:
        assert (
            settings.DOCKER_PROXY_URL  # type: ignore[attr-defined]
            == "http://docker-proxy:2375"
        )
        assert (
            settings.ADMIN_SESSION_TTL_MINUTES  # type: ignore[attr-defined]
            == 720
        )
        assert settings.ADMIN_COOKIE_SECURE is True  # type: ignore[attr-defined]
        assert settings.ADMIN_COMPOSE_PROJECT == "nodya"  # type: ignore[attr-defined]
        assert (
            settings.ADMIN_PUBLIC_ORIGIN  # type: ignore[attr-defined]
            == "https://admin.nodya.shayden.ru"
        )
        hosts: str = settings.ADMIN_ALLOWED_HOSTS  # type: ignore[attr-defined]
        assert "admin.nodya.shayden.ru" in hosts
        assert "nodya_admin" in hosts

    def test_observability_defaults(self, settings: object) -> None:
        assert settings.ERROR_EVENTS_ENABLED is True  # type: ignore[attr-defined]
        assert settings.ERROR_EVENTS_TTL_DAYS == 30  # type: ignore[attr-defined]
        assert settings.METRICS_ENABLED is True  # type: ignore[attr-defined]
        assert settings.METRICS_PORT == 8015  # type: ignore[attr-defined]

    def test_unknown_dotenv_key_rejected(self, tmp_path: object) -> None:
        """extra="forbid" must fail fast on an unknown .env key."""
        env_file = tmp_path / "local.env"  # type: ignore[operator]
        env_file.write_text("NODYA_UNKNOWN_KEY=1\n", encoding="utf-8")
        with pytest.raises(ValidationError):
            SettingsSchema(_env_file=env_file)

    def test_bounds_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SettingsSchema(LOG_RETENTION_DAYS=0)
        with pytest.raises(ValidationError):
            SettingsSchema(METRICS_PORT=80)
        with pytest.raises(ValidationError):
            SettingsSchema(ERROR_EVENTS_TTL_DAYS=0)
        with pytest.raises(ValidationError):
            SettingsSchema(ADMIN_SESSION_TTL_MINUTES=1)
