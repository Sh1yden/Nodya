"""Normalize source URLs and discover RSS/Telegram/YouTube feeds."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.brain.feed.http import open_client
from app.brain.feed.models import ResolvedSource
from app.brain.feed.text import clean_text

_TELEGRAM_RE = re.compile(r"^/(?:s/)?(?P<name>[A-Za-z][A-Za-z0-9_]{3,31})/?$")
_YOUTUBE_CHANNEL_RE = re.compile(r"^/channel/(?P<id>UC[\w-]{22})/?$")
_YOUTUBE_HANDLE_RE = re.compile(
    r"^/(?:@(?P<handle>[\w.-]+)"
    r"|c/(?P<custom>[\w.-]+)"
    r"|user/(?P<user>[\w.-]+))/?$"
)
_CHANNEL_ID_PATTERNS = (
    re.compile(r'"channelId":"(?P<id>UC[\w-]{22})"'),
    re.compile(r'"externalId":"(?P<id>UC[\w-]{22})"'),
)
_FEED_TYPES = ("application/rss+xml", "application/atom+xml")
_TITLE_LIMIT = 256


class FeedResolutionError(ValueError):
    """A URL cannot be turned into a supported feed source."""


def normalize_raw_url(raw: str) -> str:
    """Normalize a user-supplied URL.

    Args:
        raw: URL possibly without scheme or with wrapping brackets.

    Returns:
        URL with scheme, lowercase host and no fragment.

    Raises:
        FeedResolutionError: Empty or non-http(s) input.
    """
    candidate = raw.strip().strip("<>()[]").strip()
    if not candidate:
        raise FeedResolutionError("Пустая ссылка.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https"):
        raise FeedResolutionError("Поддерживаются только http(s)-ссылки.")
    if not parts.netloc:
        raise FeedResolutionError("Ссылка без домена.")
    url = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            parts.query,
            "",
        )
    )
    if len(url) > 1024:
        raise FeedResolutionError("Слишком длинная ссылка.")
    return url


async def resolve_source(
    raw: str, *, client: httpx.AsyncClient | None = None
) -> ResolvedSource:
    """Turn any supported link into a canonical feed source.

    Args:
        raw: User-supplied URL.
        client: Optional HTTP client (tests, callers).

    Returns:
        Canonical source with its fetch URL and kind.

    Raises:
        FeedResolutionError: URL is unsupported or has no usable feed.
    """
    url = normalize_raw_url(raw)
    parts = urlsplit(url)
    host = parts.netloc.removeprefix("www.")
    if host in ("t.me", "telegram.me"):
        return await _resolve_telegram(parts.path, client=client)
    if host in ("youtube.com", "m.youtube.com"):
        return await _resolve_youtube(url, parts.path, client=client)
    return await _resolve_site(url, client=client)


async def _resolve_telegram(
    path: str, *, client: httpx.AsyncClient | None
) -> ResolvedSource:
    """Validate a public Telegram channel and build its web URL.

    Args:
        path: URL path of the t.me link.
        client: Optional HTTP client.

    Returns:
        Telegram source pointing at the /s/ preview page.

    Raises:
        FeedResolutionError: Invite links, private or missing channels.
    """
    match = _TELEGRAM_RE.match(path)
    if match is None:
        raise FeedResolutionError(
            "Дай ссылку на публичный канал вида https://t.me/name."
        )
    name = match.group("name")
    url = f"https://t.me/s/{name.lower()}"
    try:
        async with open_client(client) as http:
            response = await http.get(url)
            response.raise_for_status()
    except Exception as exc:
        raise FeedResolutionError(f"Не удалось открыть канал: {exc}") from exc
    soup = BeautifulSoup(response.text, "html.parser")
    title_node = soup.select_one(".tgme_channel_info_header_title")
    title = clean_text(title_node.get_text(" ")) if title_node else ""
    if not title and not soup.select_one("div.tgme_widget_message"):
        raise FeedResolutionError(
            "Канал не найден или закрыт для веб-просмотра."
        )
    return ResolvedSource(
        url=url, kind="telegram", title=title[:_TITLE_LIMIT] or None
    )


async def _resolve_youtube(
    url: str, path: str, *, client: httpx.AsyncClient | None
) -> ResolvedSource:
    """Resolve a YouTube channel URL to its channel RSS feed.

    Args:
        url: Normalized YouTube URL.
        path: URL path of the channel link.
        client: Optional HTTP client.

    Returns:
        RSS source of the channel.

    Raises:
        FeedResolutionError: Not a channel URL or channel id not found.
    """
    channel_match = _YOUTUBE_CHANNEL_RE.match(path)
    if channel_match is not None:
        return _youtube_feed(channel_match.group("id"), title=None)
    if _YOUTUBE_HANDLE_RE.match(path) is None:
        raise FeedResolutionError(
            "Дай ссылку на канал YouTube (/@handle или /channel/…)."
        )
    try:
        async with open_client(client) as http:
            response = await http.get(url)
            response.raise_for_status()
    except Exception as exc:
        raise FeedResolutionError(
            f"Не удалось открыть канал YouTube: {exc}"
        ) from exc
    soup = BeautifulSoup(response.text, "html.parser")
    channel_id = _extract_channel_id(soup, response.text)
    if channel_id is None:
        raise FeedResolutionError(
            "Не смогла определить ID канала YouTube — проверь ссылку."
        )
    title_meta = soup.find("meta", property="og:title")
    title = clean_text(title_meta.get("content", "")) if title_meta else ""
    return _youtube_feed(channel_id, title=title or None)


def _youtube_feed(channel_id: str, *, title: str | None) -> ResolvedSource:
    """Build the RSS source of a YouTube channel."""
    return ResolvedSource(
        url=(
            f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        ),
        kind="rss",
        title=title[:_TITLE_LIMIT] if title else None,
    )


def _extract_channel_id(soup: BeautifulSoup, page_text: str) -> str | None:
    """Find a channel id in a YouTube channel page."""
    meta = soup.find("meta", attrs={"itemprop": "channelId"})
    if meta is not None:
        content = str(meta.get("content", ""))
        if re.fullmatch(r"UC[\w-]{22}", content):
            return content
    canonical = soup.find("link", rel="canonical")
    if canonical is not None:
        href = str(canonical.get("href", ""))
        match = _YOUTUBE_CHANNEL_RE.search(urlsplit(href).path)
        if match is not None:
            return match.group("id")
    for pattern in _CHANNEL_ID_PATTERNS:
        match = pattern.search(page_text)
        if match is not None:
            return match.group("id")
    return None


async def _resolve_site(
    url: str, *, client: httpx.AsyncClient | None
) -> ResolvedSource:
    """Accept an RSS feed or discover one on a site page.

    Args:
        url: Normalized site URL.
        client: Optional HTTP client.

    Returns:
        RSS source, either the URL itself or a discovered feed.

    Raises:
        FeedResolutionError: Page is unreachable or has no feed.
    """
    try:
        async with open_client(client) as http:
            response = await http.get(url)
            response.raise_for_status()
            parsed = await asyncio.to_thread(
                feedparser.parse, response.content
            )
            if _looks_like_feed(parsed):
                title = clean_text((parsed.feed or {}).get("title", ""))
                return ResolvedSource(
                    url=str(response.url),
                    kind="rss",
                    title=title[:_TITLE_LIMIT] or None,
                )
            for feed_url in _discover_feed_links(soup_of(response), response):
                resolved = await _validate_feed(http, feed_url)
                if resolved is not None:
                    return resolved
    except FeedResolutionError:
        raise
    except Exception as exc:
        raise FeedResolutionError(
            f"Не удалось открыть страницу: {exc}"
        ) from exc
    raise FeedResolutionError(
        "Не нашёл RSS/Atom-ленту на этой странице. Дай прямую ссылку на фид."
    )


def soup_of(response: httpx.Response) -> BeautifulSoup:
    """Parse an HTML response body."""
    return BeautifulSoup(response.text, "html.parser")


def _looks_like_feed(parsed: feedparser.FeedParserDict) -> bool:
    """Whether feedparser recognized the payload as a feed."""
    return bool(parsed.version or parsed.entries)


def _discover_feed_links(
    soup: BeautifulSoup, response: httpx.Response
) -> list[str]:
    """Collect alternate feed URLs advertised by an HTML page."""
    feeds: list[str] = []
    for node in soup.find_all("link"):
        rel = [str(item).lower() for item in node.get("rel") or []]
        if "alternate" not in rel:
            continue
        type_ = str(node.get("type", "")).lower()
        if type_ not in _FEED_TYPES:
            continue
        href = node.get("href")
        if not href:
            continue
        feeds.append(str(response.url.join(href)))
    feeds.sort(key=lambda link: "atom" in link.lower())
    return feeds


async def _validate_feed(
    http: httpx.AsyncClient, url: str
) -> ResolvedSource | None:
    """Fetch a discovered feed and keep it only when readable."""
    try:
        response = await http.get(url)
        response.raise_for_status()
        parsed = await asyncio.to_thread(feedparser.parse, response.content)
    except Exception:
        return None
    if not _looks_like_feed(parsed):
        return None
    title = clean_text((parsed.feed or {}).get("title", ""))
    return ResolvedSource(
        url=str(response.url),
        kind="rss",
        title=title[:_TITLE_LIMIT] or None,
    )
