"""Unit tests for ``app.brain.feed.fetchers``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.brain.feed import fetchers
from app.brain.feed.fetchers import fetch_rss, fetch_source, fetch_telegram

_RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Feed</title>
    <item>
      <title>&lt;b&gt;Новость&lt;/b&gt;</title>
      <link>https://example.com/1</link>
      <guid>entry-1</guid>
      <description>&lt;p&gt;Тело &amp;amp; ещё&lt;/p&gt;</description>
    </item>
  </channel>
</rss>
"""

_TELEGRAM_HTML = """
<html><body>
<div class="tgme_channel_info">
  <div class="tgme_channel_info_header_title">Новости Тест</div>
</div>
<div class="tgme_widget_message" data-post="testchan/1">
  <div class="tgme_widget_message_text">
    Первая строка<br/>вторая строка
  </div>
</div>
<div class="tgme_widget_message service_message" data-post="testchan/2">
  <div class="service_message">Channel created</div>
</div>
<div class="tgme_widget_message" data-post="testchan/3">
  <div class="tgme_widget_message_photo_wrap">photo</div>
</div>
</body></html>
"""


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _static(body: str, status: int = 200) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    return handler


class TestFetchRss:
    async def test_entries_are_normalized(self) -> None:
        async with _client(_static(_RSS_XML)) as client:
            items = await fetch_rss(
                "https://example.com/feed.xml", client=client
            )

        assert items is not None
        assert len(items) == 1
        assert items[0].entry_id == "entry-1"
        assert items[0].title == "Новость"
        assert items[0].summary == "Тело & ещё"
        assert items[0].link == "https://example.com/1"

    async def test_http_error_returns_none(self) -> None:
        async with _client(_static("boom", status=500)) as client:
            assert (
                await fetch_rss("https://example.com/feed.xml", client=client)
                is None
            )

    async def test_unreadable_feed_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            fetchers.feedparser,
            "parse",
            lambda content: SimpleNamespace(bozo=True, entries=[]),
        )
        async with _client(_static("<html></html>")) as client:
            assert (
                await fetch_rss("https://example.com/feed.xml", client=client)
                is None
            )


class TestFetchTelegram:
    async def test_posts_are_normalized(self) -> None:
        async with _client(_static(_TELEGRAM_HTML)) as client:
            items = await fetch_telegram(
                "https://t.me/s/testchan", client=client
            )

        assert items is not None
        assert [item.entry_id for item in items] == [
            "testchan/1",
            "testchan/3",
        ]
        first, media = items
        assert first.title == "Первая строка"
        assert first.summary == "Первая строка\nвторая строка"
        assert first.link == "https://t.me/testchan/1"
        assert media.title == "[фото]"
        assert media.summary == ""
        assert media.link == "https://t.me/testchan/3"

    async def test_page_without_channel_returns_none(self) -> None:
        page = "<html><body>nope</body></html>"
        async with _client(_static(page)) as client:
            assert (
                await fetch_telegram("https://t.me/s/testchan", client=client)
                is None
            )

    async def test_http_error_returns_none(self) -> None:
        async with _client(_static("gone", status=404)) as client:
            assert (
                await fetch_telegram("https://t.me/s/testchan", client=client)
                is None
            )

    async def test_empty_channel_returns_empty_list(self) -> None:
        page = '<div class="tgme_channel_info_header_title">Пусто</div>'
        async with _client(_static(page)) as client:
            items = await fetch_telegram(
                "https://t.me/s/testchan", client=client
            )

        assert items == []


class TestFetchSource:
    async def test_rss_dispatch(self) -> None:
        async with _client(_static(_RSS_XML)) as client:
            items = await fetch_source(
                "rss", "https://example.com/feed.xml", client=client
            )

        assert items is not None
        assert items[0].entry_id == "entry-1"

    async def test_unknown_kind_is_skipped(self) -> None:
        assert (
            await fetch_source("web", "https://example.com/feed.xml") is None
        )
