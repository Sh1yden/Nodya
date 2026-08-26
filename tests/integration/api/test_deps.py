"""Integration tests for ``app.api.deps``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.deps import get_current_user
from app.brain.models import AuthTokens, Users


def _make_user(uid: uuid.UUID | None = None) -> Users:
    return Users(
        user_id=uid or uuid.uuid4(),
        telegram_id=111,
        username="authed",
        passwd_hash="hash",
        role="user",
    )


class TestGetCurrentUserDirect:
    """Test get_current_user by calling it directly."""

    def test_valid_token_returns_user(self, mock_session: AsyncMock) -> None:
        user = _make_user()
        token_row = MagicMock(spec=AuthTokens)
        token_row.user_id = user.user_id
        token_row.revoked_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = token_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.get = AsyncMock(return_value=user)
        mock_session.commit = AsyncMock()

        with patch("app.api.deps.hash_token", return_value="hashed"):
            credentials = MagicMock()
            credentials.credentials = "raw-token-abc"
            import asyncio

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(
                get_current_user(
                    credentials=credentials,
                    session=mock_session,
                )
            )
            loop.close()
        assert result.user_id == user.user_id

    def test_no_credentials_raises(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        with pytest.raises(Exception, match="unauthorized"):
            loop.run_until_complete(
                get_current_user(
                    credentials=None,
                    session=MagicMock(),
                )
            )
        loop.close()

    def test_unknown_token_raises(self, mock_session: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("app.api.deps.hash_token", return_value="hashed"):
            credentials = MagicMock()
            credentials.credentials = "bad-token"
            import asyncio

            loop = asyncio.new_event_loop()
            with pytest.raises(Exception, match="unauthorized"):
                loop.run_until_complete(
                    get_current_user(
                        credentials=credentials,
                        session=mock_session,
                    )
                )
            loop.close()

    def test_revoked_token_raises(self, mock_session: AsyncMock) -> None:
        token_row = MagicMock(spec=AuthTokens)
        token_row.revoked_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = token_row
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("app.api.deps.hash_token", return_value="hashed"):
            credentials = MagicMock()
            credentials.credentials = "revoked-token"
            import asyncio

            loop = asyncio.new_event_loop()
            with pytest.raises(Exception, match="unauthorized"):
                loop.run_until_complete(
                    get_current_user(
                        credentials=credentials,
                        session=mock_session,
                    )
                )
            loop.close()
