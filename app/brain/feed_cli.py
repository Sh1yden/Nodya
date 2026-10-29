"""CLI for feed source management and manual discovery.

Usage:
    uv run python -m app.brain.feed_cli add <url>
    uv run python -m app.brain.feed_cli list
    uv run python -m app.brain.feed_cli rm <id|url>
    uv run python -m app.brain.feed_cli discover
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.api.messaging import MessagePublisher
from app.brain.feed import FeedResolutionError, FeedSourceService
from app.brain.feed.discovery import FeedDiscoveryJob
from app.brain.llm_choice import (
    GeminiCloudflareProvider,
    LLMRouter,
    OpenRouterProvider,
    ProviderRegistry,
)
from app.brain.memory.long import AsyncSessionLocal, engine
from app.brain.search import SearchChain
from app.core import settings


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(description="Manage Nodya feed sources")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_parser = subparsers.add_parser("add", help="Add a source by URL")
    add_parser.add_argument("url", help="RSS, Telegram or YouTube URL")
    subparsers.add_parser("list", help="List every source")
    rm_parser = subparsers.add_parser("rm", help="Disable a source")
    rm_parser.add_argument("ref", help="Short id or URL")
    subparsers.add_parser("discover", help="Run interest-based discovery once")
    return parser.parse_args(argv)


def _build_router() -> LLMRouter:
    """Assemble the LLM router like the application does."""
    registry = ProviderRegistry(settings)
    registry.register(
        "gemini_cloudflare", GeminiCloudflareProvider, enabled=True
    )
    registry.register("openrouter", OpenRouterProvider, enabled=True)
    if settings.GEMINI_ENABLED:
        from app.brain.llm_choice.gemini import GeminiProvider

        registry.register("gemini", GeminiProvider, enabled=True)
    return LLMRouter(registry=registry)


async def _discover(service: FeedSourceService) -> int:
    """Run one discovery pass with real dependencies."""
    router = _build_router()
    publisher = MessagePublisher(settings.rabbitmq_url)
    chain = SearchChain.from_settings()
    try:
        await publisher.connect()
        job = FeedDiscoveryJob(
            session_factory=AsyncSessionLocal,
            router=router,
            search_chain=chain,
            service=service,
            publisher=publisher,
        )
        added = await job.run()
        print(f"Добавлено источников: {added}")
        return 0
    finally:
        await publisher.close()
        await router.close()
        await chain.close()


async def _main(argv: list[str] | None = None) -> int:
    """CLI entry; returns the process exit code."""
    args = _parse_args(argv)
    service = FeedSourceService(AsyncSessionLocal)
    try:
        if args.command == "add":
            try:
                result = await service.add(args.url)
            except (FeedResolutionError, ValueError) as exc:
                print(f"Ошибка: {exc}", file=sys.stderr)
                return 1
            labels = {
                "added": "Добавлено",
                "reactivated": "Включено",
                "exists": "Уже отслеживается",
            }
            print(
                f"{labels[result.status]}: "
                f"{result.source.title or result.source.url} "
                f"[{result.source.kind}] {result.source.url}"
            )
            return 0
        if args.command == "list":
            sources = await service.list()
            if not sources:
                print("Источников нет.")
                return 0
            for source in sources:
                state = "on" if source.is_active else "off"
                print(
                    f"{source.source_id.hex[:8]} | {state} | "
                    f"{source.kind} | {source.title or source.url} | "
                    f"{source.url}"
                )
            return 0
        if args.command == "rm":
            removed = await service.deactivate(args.ref)
            if removed is None:
                print("Источник не найден.", file=sys.stderr)
                return 1
            if not removed.changed:
                print("Источник уже выключен.")
                return 0
            print(f"Источник выключен: {removed.source.url}")
            return 0
        return await _discover(service)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
