"""Unit tests for search providers and the fallback chain."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.brain.search.base import SearchChain, SearchResult
from app.brain.search.providers import (
    BraveProvider,
    DuckDuckGoProvider,
    TavilyProvider,
)


class _Provider:
    def __init__(
        self,
        name: str,
        *,
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self._results = results or []
        self._error = error
        self.calls = 0
        self.closed = False

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._results)

    async def close(self) -> None:
        self.closed = True


_DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a"
     href="/l/?uddg=https%3A%2F%2Fexample.com%2Fnews&amp;rut=x">
    Example News
  </a>
  <a class="result__snippet">First snippet</a>
</div>
<div class="result">
  <a class="result__a" href="https://direct.example/post">Direct</a>
  <a class="result__snippet">Second snippet</a>
</div>
</body></html>
"""


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestSearchChain:
    async def test_without_keys_only_duckduckgo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.search.base.settings.SEARCH_TAVILY_API_KEY", ""
        )
        monkeypatch.setattr(
            "app.brain.search.base.settings.SEARCH_BRAVE_API_KEY", ""
        )

        chain = SearchChain.from_settings()
        try:
            assert chain.names == ["duckduckgo"]
            assert chain.available is True
        finally:
            await chain.close()

    async def test_tavily_precedes_duckduckgo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.search.base.settings.SEARCH_TAVILY_API_KEY", "t"
        )
        monkeypatch.setattr(
            "app.brain.search.base.settings.SEARCH_BRAVE_API_KEY", ""
        )

        chain = SearchChain.from_settings()
        try:
            assert chain.names == ["tavily", "duckduckgo"]
        finally:
            await chain.close()

    async def test_full_chain_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.brain.search.base.settings.SEARCH_TAVILY_API_KEY", "t"
        )
        monkeypatch.setattr(
            "app.brain.search.base.settings.SEARCH_BRAVE_API_KEY", "b"
        )

        chain = SearchChain.from_settings()
        try:
            assert chain.names == ["tavily", "brave", "duckduckgo"]
        finally:
            await chain.close()

    async def test_fallback_after_error(self) -> None:
        first = _Provider("first", error=RuntimeError("429"))
        second = _Provider(
            "second", results=[SearchResult(title="A", url="https://a")]
        )
        chain = SearchChain([first, second])  # type: ignore[list-item]

        results = await chain.search("q", 5)

        assert [result.url for result in results] == ["https://a"]
        assert first.calls == 1
        assert second.calls == 1

    async def test_fallback_after_empty_result(self) -> None:
        first = _Provider("first")
        second = _Provider(
            "second", results=[SearchResult(title="B", url="https://b")]
        )
        chain = SearchChain([first, second])  # type: ignore[list-item]

        results = await chain.search("q", 5)

        assert [result.url for result in results] == ["https://b"]
        assert second.calls == 1

    async def test_first_success_stops_chain(self) -> None:
        first = _Provider(
            "first", results=[SearchResult(title="A", url="https://a")]
        )
        second = _Provider(
            "second", results=[SearchResult(title="B", url="https://b")]
        )
        chain = SearchChain([first, second])  # type: ignore[list-item]

        results = await chain.search("q", 5)

        assert [result.url for result in results] == ["https://a"]
        assert second.calls == 0

    async def test_all_fail_returns_empty(self) -> None:
        chain = SearchChain(
            [
                _Provider("a", error=RuntimeError("down")),
                _Provider("b"),
            ]  # type: ignore[list-item]
        )

        assert await chain.search("q", 5) == []

    async def test_close_releases_every_provider(self) -> None:
        first = _Provider("a")
        second = _Provider("b")
        chain = SearchChain([first, second])  # type: ignore[list-item]

        await chain.close()

        assert first.closed is True
        assert second.closed is True


class TestTavilyProvider:
    async def test_parses_results(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["auth"] = request.headers.get("Authorization")
            seen["body"] = request.content.decode()
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "A",
                            "url": "https://a.example",
                            "content": "snippet",
                        },
                        {"title": "No URL"},
                    ]
                },
            )

        async with _client(handler) as client:
            provider = TavilyProvider("secret", client=client)
            results = await provider.search("ai news", 5)

        assert [result.url for result in results] == ["https://a.example"]
        assert results[0].snippet == "snippet"
        assert seen["url"] == "https://api.tavily.com/search"
        assert seen["method"] == "POST"
        assert seen["auth"] == "Bearer secret"
        assert '"max_results":5' in seen["body"]

    async def test_http_error_raises(self) -> None:
        async with _client(
            lambda request: httpx.Response(429, text="slow down")
        ) as client:
            provider = TavilyProvider("secret", client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await provider.search("q", 5)


class TestBraveProvider:
    async def test_parses_results(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["token"] = request.headers.get("X-Subscription-Token")
            return httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "B",
                                "url": "https://b.example",
                                "description": "desc",
                            }
                        ]
                    }
                },
            )

        async with _client(handler) as client:
            provider = BraveProvider("brave-key", client=client)
            results = await provider.search("python", 3)

        assert results == [
            SearchResult(title="B", url="https://b.example", snippet="desc")
        ]
        assert seen["url"].startswith("https://api.search.brave.com/")
        assert "q=python" in seen["url"]
        assert seen["token"] == "brave-key"


class TestDuckDuckGoProvider:
    async def test_parses_redirect_and_direct_links(self) -> None:
        async with _client(
            lambda request: httpx.Response(200, text=_DDG_HTML)
        ) as client:
            provider = DuckDuckGoProvider(client=client)
            results = await provider.search("news", 5)

        assert results == [
            SearchResult(
                title="Example News",
                url="https://example.com/news",
                snippet="First snippet",
            ),
            SearchResult(
                title="Direct",
                url="https://direct.example/post",
                snippet="Second snippet",
            ),
        ]

    async def test_limit_is_respected(self) -> None:
        async with _client(
            lambda request: httpx.Response(200, text=_DDG_HTML)
        ) as client:
            provider = DuckDuckGoProvider(client=client)
            results = await provider.search("news", 1)

        assert len(results) == 1

    async def test_empty_page_returns_empty(self) -> None:
        async with _client(
            lambda request: httpx.Response(200, text="<html></html>")
        ) as client:
            provider = DuckDuckGoProvider(client=client)

            assert await provider.search("news", 5) == []
