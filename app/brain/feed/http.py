"""HTTP client helper for feed fetching and resolving."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.core import settings

FEED_USER_AGENT = "NodyaFeedParser/1.0"


@asynccontextmanager
async def open_client(
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an injected client or a temporary configured one.

    Args:
        client: Client supplied by the caller (tests, batch fetch).

    Yields:
        Client with feed timeouts, redirects and the parser UA.
    """
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(
        timeout=settings.FEED_REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": FEED_USER_AGENT},
    ) as own:
        yield own
