"""Unit tests for ``app.brain.feed.resolver``."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.brain.feed.resolver import (
    FeedResolutionError,
    normalize_raw_url,
    resolve_source,
)

_CHANNEL_ID = "UC" + "a" * 22

_FEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Site Feed</title>
    <item>
      <title>Новость</title>
      <link>https://news.example/1</link>
      <guid>entry-1</guid>
    </item>
  </channel>
</rss>
"""

_TELEGRAM_PAGE = """
<html><body>
<div class="tgme_channel_info">
  <div class="tgme_channel_info_header_title">Канал Тест</div>
</div>
<div class="tgme_widget_message" data-post="testchan/1">
  <div class="tgme_widget_message_text">Привет</div>
</div>
</body></html>
"""


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _routed(routes: dict[str, tuple[int, str]]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(request.url.path, (404, "not found"))
        return httpx.Response(status, text=body)

    return handler


class TestNormalizeRawUrl:
    def test_adds_scheme(self) -> None:
        assert (
            normalize_raw_url("example.com/feed") == "https://example.com/feed"
        )

    def test_lowercases_host_and_drops_fragment(self) -> None:
        result = normalize_raw_url("https://Example.COM/Path#frag")
        assert result == "https://example.com/Path"

    def test_strips_wrapping_brackets(self) -> None:
        result = normalize_raw_url("(https://example.com/feed)")
        assert result == "https://example.com/feed"

    def test_rejects_empty(self) -> None:
        with pytest.raises(FeedResolutionError, match="Пустая ссылка"):
            normalize_raw_url("   ")

    def test_rejects_non_http_scheme(self) -> None:
        with pytest.raises(FeedResolutionError, match="http"):
            normalize_raw_url("ftp://example.com/feed")

    def test_rejects_missing_host(self) -> None:
        with pytest.raises(FeedResolutionError, match="домена"):
            normalize_raw_url("https:///feed")

    def test_rejects_overlong_url(self) -> None:
        with pytest.raises(FeedResolutionError, match="длинная"):
            normalize_raw_url("https://example.com/" + "a" * 1100)


class TestTelegramResolution:
    async def test_public_channel_is_resolved(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, text=_TELEGRAM_PAGE)

        resolved = await resolve_source(
            "https://t.me/testchan", client=_client(handler)
        )

        assert resolved.url == "https://t.me/s/testchan"
        assert resolved.kind == "telegram"
        assert resolved.title == "Канал Тест"
        assert seen == ["/s/testchan"]

    async def test_invite_link_is_rejected(self) -> None:
        with pytest.raises(FeedResolutionError, match="публичный канал"):
            await resolve_source("https://t.me/+abc123")

    async def test_joinchat_link_is_rejected(self) -> None:
        with pytest.raises(FeedResolutionError, match="публичный канал"):
            await resolve_source("https://t.me/joinchat/abc123")

    async def test_missing_channel_is_rejected(self) -> None:
        async with _client(_routed({"/s/testchan": (404, "no")})) as client:
            with pytest.raises(FeedResolutionError, match="открыть канал"):
                await resolve_source("https://t.me/testchan", client=client)


class TestYoutubeResolution:
    async def test_channel_id_link_builds_feed(self) -> None:
        resolved = await resolve_source(
            f"https://www.youtube.com/channel/{_CHANNEL_ID}"
        )

        assert resolved.url == (
            "https://www.youtube.com/feeds/videos.xml"
            f"?channel_id={_CHANNEL_ID}"
        )
        assert resolved.kind == "rss"

    async def test_handle_page_yields_feed_and_title(self) -> None:
        page = (
            "<html><head>"
            f'<meta itemprop="channelId" content="{_CHANNEL_ID}">'
            '<meta property="og:title" content="Тестовый канал">'
            "</head></html>"
        )
        async with _client(_routed({"/@testchan": (200, page)})) as client:
            resolved = await resolve_source(
                "https://www.youtube.com/@testchan", client=client
            )

        assert resolved.url.endswith(f"channel_id={_CHANNEL_ID}")
        assert resolved.kind == "rss"
        assert resolved.title == "Тестовый канал"

    async def test_canonical_link_fallback(self) -> None:
        page = (
            "<html><head>"
            f'<link rel="canonical" '
            f'href="https://www.youtube.com/channel/{_CHANNEL_ID}">'
            "</head></html>"
        )
        async with _client(_routed({"/c/testchan": (200, page)})) as client:
            resolved = await resolve_source(
                "https://www.youtube.com/c/testchan", client=client
            )

        assert resolved.url.endswith(f"channel_id={_CHANNEL_ID}")

    async def test_unknown_channel_id_is_rejected(self) -> None:
        async with _client(
            _routed({"/@testchan": (200, "<html></html>")})
        ) as client:
            with pytest.raises(FeedResolutionError, match="ID канала"):
                await resolve_source(
                    "https://www.youtube.com/@testchan", client=client
                )

    async def test_non_channel_link_is_rejected(self) -> None:
        with pytest.raises(FeedResolutionError, match="канал YouTube"):
            await resolve_source("https://www.youtube.com/watch?v=123")


class TestSiteResolution:
    async def test_direct_feed_is_kept(self) -> None:
        async with _client(_routed({"/feed.xml": (200, _FEED_XML)})) as client:
            resolved = await resolve_source(
                "https://news.example/feed.xml", client=client
            )

        assert resolved.url == "https://news.example/feed.xml"
        assert resolved.kind == "rss"
        assert resolved.title == "Site Feed"

    async def test_alternate_link_is_discovered(self) -> None:
        page = (
            "<html><head>"
            '<link rel="alternate" type="application/rss+xml" '
            'href="/rss.xml">'
            "</head><body>site</body></html>"
        )
        routes = {"/": (200, page), "/rss.xml": (200, _FEED_XML)}
        async with _client(_routed(routes)) as client:
            resolved = await resolve_source(
                "https://news.example/", client=client
            )

        assert resolved.url == "https://news.example/rss.xml"
        assert resolved.kind == "rss"
        assert resolved.title == "Site Feed"

    async def test_site_without_feed_is_rejected(self) -> None:
        async with _client(
            _routed({"/": (200, "<html><body>hi</body></html>")})
        ) as client:
            with pytest.raises(FeedResolutionError, match="Не нашёл RSS/Atom"):
                await resolve_source("https://news.example/", client=client)

    async def test_page_failure_is_reported(self) -> None:
        async with _client(_routed({})) as client:
            with pytest.raises(
                FeedResolutionError, match="Не удалось открыть страницу"
            ):
                await resolve_source("https://news.example/", client=client)

    async def test_unreadable_alternate_feed_is_skipped(self) -> None:
        page = (
            "<html><head>"
            '<link rel="alternate" type="application/rss+xml" '
            'href="/rss.xml">'
            "</head></html>"
        )
        routes = {"/": (200, page), "/rss.xml": (200, "<html>nope</html>")}
        async with _client(_routed(routes)) as client:
            with pytest.raises(FeedResolutionError, match="Не нашёл RSS/Atom"):
                await resolve_source("https://news.example/", client=client)
