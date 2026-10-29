"""Search provider protocol and fallback chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core import get_logger, settings

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One web search hit."""

    title: str
    url: str
    snippet: str = ""


class SearchProvider(Protocol):
    """Minimal interface of a web search backend."""

    name: str

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        """Run one query and return its hits."""
        ...

    async def close(self) -> None:
        """Release network resources."""
        ...


class SearchChain:
    """Try providers in priority order until one answers."""

    def __init__(self, providers: list[SearchProvider]) -> None:
        """Store providers ordered best-first.

        Args:
            providers: Configured providers; empty disables search.
        """
        self._providers = providers

    @classmethod
    def from_settings(cls) -> SearchChain:
        """Build the Tavily -> Brave -> DuckDuckGo chain.

        Providers without an API key are skipped; DuckDuckGo needs no
        key and is always appended as the last resort.
        """
        from app.brain.search.providers import (
            BraveProvider,
            DuckDuckGoProvider,
            TavilyProvider,
        )

        providers: list[SearchProvider] = []
        if settings.SEARCH_TAVILY_API_KEY:
            providers.append(TavilyProvider(settings.SEARCH_TAVILY_API_KEY))
        if settings.SEARCH_BRAVE_API_KEY:
            providers.append(BraveProvider(settings.SEARCH_BRAVE_API_KEY))
        providers.append(DuckDuckGoProvider())
        return cls(providers)

    @property
    def available(self) -> bool:
        """Whether at least one provider is configured."""
        return bool(self._providers)

    @property
    def names(self) -> list[str]:
        """Names of configured providers, best-first."""
        return [provider.name for provider in self._providers]

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        """Run a query, falling back to the next provider on failure.

        Args:
            query: Search query text.
            limit: Max hits requested from a provider.

        Returns:
            Hits from the first provider that produced any, or an
            empty list when every provider failed or was empty.
        """
        for provider in self._providers:
            try:
                results = await provider.search(query, limit)
            except Exception as exc:
                logger.warning(
                    f"Search provider {provider.name} failed: {exc}"
                )
                continue
            if results:
                return results
            logger.debug(f"Search provider {provider.name} returned nothing.")
        return []

    async def close(self) -> None:
        """Close every provider that owns a network client."""
        for provider in self._providers:
            try:
                await provider.close()
            except Exception as exc:
                logger.warning(
                    f"Search provider close failed {provider.name}: {exc}"
                )
