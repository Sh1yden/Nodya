"""Web search providers: Tavily, Brave and DuckDuckGo HTML."""

from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.brain.search.base import SearchResult
from app.core import settings

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_TAVILY_URL = "https://api.tavily.com/search"
_DDG_URL = "https://html.duckduckgo.com/html/"
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _client_or_own(
    client: httpx.AsyncClient | None,
) -> tuple[httpx.AsyncClient, bool]:
    """Return the injected client or a new one plus ownership flag."""
    if client is not None:
        return client, False
    return (
        httpx.AsyncClient(
            timeout=settings.SEARCH_REQUEST_TIMEOUT,
            follow_redirects=True,
        ),
        True,
    )


class TavilyProvider:
    """Tavily search API (agent-oriented)."""

    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Store the key and the HTTP client.

        Args:
            api_key: Tavily API key.
            client: Optional injected client (tests).
        """
        self._api_key = api_key
        self._client, self._own = _client_or_own(client)

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        """Run one query through Tavily."""
        response = await self._client.post(
            _TAVILY_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
            },
        )
        response.raise_for_status()
        data = response.json()
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "")),
            )
            for item in data.get("results", [])
            if item.get("url")
        ][:limit]

    async def close(self) -> None:
        """Close the owned client."""
        if self._own:
            await self._client.aclose()


class BraveProvider:
    """Brave Web Search API."""

    name = "brave"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Store the key and the HTTP client.

        Args:
            api_key: Brave Search API key.
            client: Optional injected client (tests).
        """
        self._api_key = api_key
        self._client, self._own = _client_or_own(client)

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        """Run one query through Brave."""
        response = await self._client.get(
            _BRAVE_URL,
            params={"q": query, "count": limit},
            headers={
                "X-Subscription-Token": self._api_key,
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()
        items = (data.get("web") or {}).get("results") or []
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("description", "")),
            )
            for item in items
            if item.get("url")
        ][:limit]

    async def close(self) -> None:
        """Close the owned client."""
        if self._own:
            await self._client.aclose()


class DuckDuckGoProvider:
    """Keyless DuckDuckGo HTML endpoint (scraping, last resort)."""

    name = "duckduckgo"

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        """Store the HTTP client.

        Args:
            client: Optional injected client (tests).
        """
        self._client, self._own = _client_or_own(client)

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        """Run one query against the HTML endpoint."""
        response = await self._client.get(
            _DDG_URL,
            params={"q": query},
            headers={"User-Agent": _BROWSER_UA},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for block in soup.select(".result"):
            link = block.select_one(".result__a")
            if link is None:
                continue
            url = _decode_redirect(str(link.get("href", "")))
            if not url:
                continue
            snippet = block.select_one(".result__snippet")
            results.append(
                SearchResult(
                    title=link.get_text(" ", strip=True),
                    url=url,
                    snippet=(
                        snippet.get_text(" ", strip=True)
                        if snippet is not None
                        else ""
                    ),
                )
            )
            if len(results) >= limit:
                break
        return results

    async def close(self) -> None:
        """Close the owned client."""
        if self._own:
            await self._client.aclose()


def _decode_redirect(href: str) -> str:
    """Unwrap DuckDuckGo redirect links into the target URL."""
    if not href:
        return ""
    absolute = urljoin("https://duckduckgo.com", href)
    parts = urlsplit(absolute)
    if parts.netloc.endswith("duckduckgo.com") and parts.path == "/l/":
        target = parse_qs(parts.query).get("uddg")
        if target:
            return target[0]
        return ""
    if parts.scheme in ("http", "https"):
        return absolute
    return ""
