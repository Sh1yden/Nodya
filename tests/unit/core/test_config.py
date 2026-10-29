"""Unit tests for ``app.core.config``."""

from __future__ import annotations

import pytest

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
