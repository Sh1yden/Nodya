"""Integration tests for ``app.worker``."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from app.worker import Worker, _PendingBatch


def _make_session_factory(
    session: AsyncMock,
) -> AsyncGenerator:
    """Return an async context manager factory."""

    @asynccontextmanager
    async def _factory() -> AsyncGenerator:
        yield session

    return _factory


class TestWorkerResolveUser:
    @patch("app.worker.settings")
    def test_generated_username_format(self, mock_settings: Mock) -> None:
        mock_settings.OWNER_USERNAME = "owner"
        result = Worker._generated_username(123456789)
        assert result == "tg_123456789"

    def test_generated_username_truncation(self) -> None:
        result = Worker._generated_username(123456789012345678)
        assert len(result) <= 20

    @patch("app.worker.settings")
    async def test_resolve_user_new_auto_registers(
        self, mock_settings: Mock
    ) -> None:
        mock_settings.OWNER_USERNAME = "owner"

        worker = Worker.__new__(Worker)
        logger = MagicMock()
        object.__setattr__(worker, "_Worker__logger", logger)

        session = AsyncMock()
        object.__setattr__(
            worker,
            "_session_factory",
            _make_session_factory(session),
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.add = MagicMock()

        await worker._resolve_user("telegram", "99999")
        session.add.assert_called_once()
        session.commit.assert_awaited()


class TestWorkerLinkCommand:
    def test_link_pattern_matches(self) -> None:
        from app.worker import _LINK_COMMAND_PATTERN

        m = _LINK_COMMAND_PATTERN.match("/link ABCD1234")
        assert m is not None
        assert m.group(1) == "ABCD1234"

    def test_start_pattern_matches(self) -> None:
        from app.worker import _LINK_COMMAND_PATTERN

        m = _LINK_COMMAND_PATTERN.match("/start ABCD1234")
        assert m is not None
        assert m.group(1) == "ABCD1234"

    def test_link_pattern_rejects_short_code(self) -> None:
        from app.worker import _LINK_COMMAND_PATTERN

        m = _LINK_COMMAND_PATTERN.match("/link ABC")
        assert m is None

    def test_link_pattern_rejects_invalid_chars(self) -> None:
        from app.worker import _LINK_COMMAND_PATTERN

        m = _LINK_COMMAND_PATTERN.match("/link ABCD123!")
        assert m is None

    def test_case_insensitive(self) -> None:
        from app.worker import _LINK_COMMAND_PATTERN

        m = _LINK_COMMAND_PATTERN.match("/LINK abcd1234")
        assert m is not None


class TestWorkerDebounce:
    async def test_pending_batch_initialization(self) -> None:
        batch = _PendingBatch()
        assert batch.amqp_messages == []
        assert batch.dtos == []


class TestWorkerProactiveDecision:
    def test_always_returns_now(self) -> None:
        result = Worker._proactive_decision()
        assert result == "now"
