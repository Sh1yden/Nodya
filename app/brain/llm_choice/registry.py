"""Provider registry with lazy initialization."""

from collections.abc import Callable

from app.core.config import SettingsSchema

from .base import LLMProvider


class ProviderDisabledError(RuntimeError):
    """Raised when a disabled provider is requested."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Provider '{name}' is disabled")


class ProviderRegistry:
    """Central registry for LLM providers.

    Providers are registered as factories and instantiated lazily
    on first access via `get()`.
    """

    def __init__(self, settings: SettingsSchema) -> None:
        self._settings = settings
        self._factories: dict[
            str, tuple[Callable[[SettingsSchema], LLMProvider], bool]
        ] = {}
        self._instances: dict[str, LLMProvider] = {}

    def register(
        self,
        name: str,
        factory: Callable[[SettingsSchema], LLMProvider],
        *,
        enabled: bool = True,
    ) -> None:
        """Register a provider factory.

        Args:
            name: Unique provider identifier (e.g., "gemini_cloudflare").
            factory: Callable accepting SettingsSchema, returning LLMProvider.
            enabled: If False, `get()` will raise ProviderDisabledError.
        """
        self._factories[name] = (factory, enabled)

    def get(self, name: str) -> LLMProvider:
        """Get or create a provider instance.

        Args:
            name: Provider name as registered.

        Returns:
            LLMProvider instance.

        Raises:
            KeyError: Provider not registered.
            ProviderDisabledError: Provider registered but disabled.
        """
        if name not in self._factories:
            raise KeyError(f"Provider '{name}' not registered")

        if name not in self._instances:
            factory, enabled = self._factories[name]
            if not enabled:
                raise ProviderDisabledError(name)
            self._instances[name] = factory(self._settings)

        return self._instances[name]

    async def close_all(self) -> None:
        """Close all instantiated providers (await aclose if needed)."""
        import asyncio

        tasks: list[asyncio.Task] = []
        for instance in self._instances.values():
            close = getattr(instance, "close", None)
            if close:
                if asyncio.iscoroutinefunction(close):
                    tasks.append(asyncio.create_task(close()))
                else:
                    close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._instances.clear()

    def is_registered(self, name: str) -> bool:
        """Check if a provider is registered (regardless of enabled)."""
        return name in self._factories

    def is_enabled(self, name: str) -> bool:
        """Check if a provider is registered and enabled."""
        if name not in self._factories:
            return False
        return self._factories[name][1]

    def list_providers(self) -> dict[str, bool]:
        """Return all registered providers with their enabled status."""
        return {
            name: enabled for name, (_, enabled) in self._factories.items()
        }
