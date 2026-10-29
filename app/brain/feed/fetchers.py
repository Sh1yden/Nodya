"""Fetchers of RSS and public Telegram channel sources."""

from __future__ import annotations

import asyncio

import feedparser
import httpx
from bs4 import BeautifulSoup, Tag

from app.brain.feed.http import open_client
from app.brain.feed.models import FeedItem
from app.brain.feed.text import clean_text
from app.core import get_logger, settings

logger = get_logger(__name__)

_SUMMARY_LIMIT = 1000
_TITLE_LIMIT = 160


async def fetch_source(
    kind: str, url: str, *, client: httpx.AsyncClient | None = None
) -> list[FeedItem] | None:
    """Fetch and normalize entries of one source.

    Args:
        kind: Source kind ("rss" or "telegram").
        url: Canonical fetch URL.
        client: Optional HTTP client.

    Returns:
        Normalized entries, or None on failure.
    """
    if kind == "rss":
        return await fetch_rss(url, client=client)
    if kind == "telegram":
        return await fetch_telegram(url, client=client)
    logger.warning(f"Feed kind '{kind}' is not supported: {url}")
    return None


async def fetch_rss(
    url: str, *, client: httpx.AsyncClient | None = None
) -> list[FeedItem] | None:
    """Download and normalize an RSS/Atom feed.

    Degraded mode: any network/parse failure logs ERROR and returns
    None so the source timestamp stays untouched.

    Args:
        url: Feed URL.
        client: Optional HTTP client.

    Returns:
        Up to FEED_MAX_ITEMS_PER_SOURCE entries, or None on failure.
    """
    try:
        async with open_client(client) as http:
            response = await http.get(url)
            response.raise_for_status()
        parsed = await asyncio.to_thread(feedparser.parse, response.content)
    except Exception as exc:
        logger.error(f"Feed fetch failed ({url}): {exc}")
        return None
    if parsed.bozo and not parsed.entries:
        logger.error(f"Feed is unreadable: {url}")
        return None
    items: list[FeedItem] = []
    for entry in parsed.entries[: settings.FEED_MAX_ITEMS_PER_SOURCE]:
        title = clean_text(entry.get("title", ""))
        if not title:
            continue
        link = str(entry.get("link", ""))
        entry_id = str(entry.get("id") or link or title)
        raw_summary = entry.get("summary") or entry.get("description")
        items.append(
            FeedItem(
                entry_id=entry_id,
                title=title,
                summary=clean_text(raw_summary or "")[:_SUMMARY_LIMIT],
                link=link,
            )
        )
    return items


async def fetch_telegram(
    url: str, *, client: httpx.AsyncClient | None = None
) -> list[FeedItem] | None:
    """Scrape the public web preview of a Telegram channel.

    Args:
        url: Channel preview URL (https://t.me/s/<name>).
        client: Optional HTTP client.

    Returns:
        Up to FEED_MAX_ITEMS_PER_SOURCE newest posts, or None when
        the page is unreachable or is not a channel preview.
    """
    try:
        async with open_client(client) as http:
            response = await http.get(url)
            response.raise_for_status()
    except Exception as exc:
        logger.error(f"Telegram fetch failed ({url}): {exc}")
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    nodes = soup.select("div.tgme_widget_message[data-post]")
    if not nodes:
        if soup.select_one(".tgme_channel_info_header_title") is None:
            logger.error(f"Telegram page is not a channel: {url}")
            return None
        return []
    items: list[FeedItem] = []
    for node in nodes[-settings.FEED_MAX_ITEMS_PER_SOURCE :]:
        if _is_service_message(node):
            continue
        data_post = str(node.get("data-post") or "").strip()
        if not data_post:
            continue
        text_node = node.select_one(".tgme_widget_message_text")
        text = _plain_text(text_node) if text_node is not None else ""
        if text:
            title = text.splitlines()[0][:_TITLE_LIMIT]
        else:
            title = _media_label(node)
        items.append(
            FeedItem(
                entry_id=data_post,
                title=title,
                summary=text[:_SUMMARY_LIMIT],
                link=f"https://t.me/{data_post}",
            )
        )
    return items


def _is_service_message(node: Tag) -> bool:
    """Whether a message node is a service event, not a post."""
    classes = node.get("class") or []
    if "service_message" in classes:
        return True
    return node.select_one(".service_message") is not None


def _plain_text(node: Tag) -> str:
    """Extract multiline plain text from a post body."""
    for br in node.find_all("br"):
        br.replace_with("\n")
    raw = node.get_text()
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _media_label(node: Tag) -> str:
    """Human-readable marker for a post without text."""
    if node.select_one(".tgme_widget_message_photo_wrap") is not None:
        return "[фото]"
    if node.select_one(".tgme_widget_message_video_player") is not None:
        return "[видео]"
    if node.select_one(".tgme_widget_message_document_wrap") is not None:
        return "[файл]"
    if node.select_one(".tgme_widget_message_sticker") is not None:
        return "[стикер]"
    return "[медиа]"
