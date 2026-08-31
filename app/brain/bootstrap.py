"""Bootstrap CLI to create the owner account (ADR-16).

Usage:
    uv run python -m app.brain.bootstrap --username Shayden --password <pwd>

Creates a single owner; fails if one already exists. Uses advisory
lock to serialize concurrent bootstraps.
"""

import argparse
import asyncio
import sys
from uuid import uuid4

from sqlalchemy import select, text

from app.brain.memory.long import AsyncSessionLocal, engine
from app.brain.models import Users
from app.brain.repositories.security import hash_password
from app.core import get_logger

logger = get_logger(__name__)

_ADVISORY_LOCK_KEY = 0x4E4F4459  # "NODY"


async def create_owner(username: str, password: str) -> Users:
    """Create the owner account.

    Args:
        username: Owner username.
        password: Owner password (will be argon2-hashed).

    Returns:
        Created Users row.

    Raises:
        RuntimeError: Owner already exists.
    """
    async with AsyncSessionLocal() as session:
        # Serialize concurrent bootstraps.
        await session.execute(
            text(f"SELECT pg_advisory_xact_lock({_ADVISORY_LOCK_KEY})")
        )
        existing = await session.scalar(
            select(Users).where(Users.role == "owner")
        )
        if existing is not None:
            raise RuntimeError(
                f"Owner already exists: {existing.username} "
                f"({existing.user_id})"
            )
        # Also prevent duplicate username hijacking.
        dup = await session.scalar(
            select(Users).where(Users.username == username)
        )
        if dup is not None:
            raise RuntimeError(f"Username '{username}' already taken.")

        user = Users(
            user_id=uuid4(),
            username=username,
            passwd_hash=hash_password(password),
            has_usable_password=True,
            role="owner",
            settings={},
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info(f"Owner created: {user.username} ({user.user_id})")
        return user


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Create owner account")
    parser.add_argument("--username", required=True, help="Owner username")
    parser.add_argument("--password", required=True, help="Owner password")
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> None:
    """CLI entry."""
    args = _parse_args(argv)
    try:
        user = await create_owner(args.username, args.password)
        print(f"Owner created: {user.username} {user.user_id}")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
