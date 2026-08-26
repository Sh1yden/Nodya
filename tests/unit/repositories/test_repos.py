"""Unit tests for ``app.brain.repositories``."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.brain.models import AuditLogs, HardFacts, Users
from app.brain.repositories import (
    AuditLogsRepo,
    BaseRepo,
    HardFactsRepo,
    UsersRepo,
)


class TestBaseRepo:
    """Test generic CRUD operations on BaseRepo."""

    @pytest.fixture()
    def repo(self) -> BaseRepo[Users]:
        session = AsyncMock()
        return BaseRepo(session, Users)

    async def test_get_by_id_delegates_to_session(
        self, repo: BaseRepo[Users]
    ) -> None:
        uid = uuid.uuid4()
        expected = Users(
            user_id=uid,
            telegram_id=1,
            username="t",
            passwd_hash="h",
            role="user",
        )
        repo.session.get = AsyncMock(return_value=expected)

        result = await repo.get_by_id(uid)
        assert result is expected
        repo.session.get.assert_awaited_once_with(Users, uid)

    async def test_get_by_id_returns_none(self, repo: BaseRepo[Users]) -> None:
        repo.session.get = AsyncMock(return_value=None)
        result = await repo.get_by_id(uuid.uuid4())
        assert result is None

    async def test_get_by_field_invalid_field(
        self, repo: BaseRepo[Users]
    ) -> None:
        with pytest.raises(ValueError, match="not exist"):
            await repo.get_by_field("nonexistent_field", "val")

    async def test_get_by_field_valid(self, repo: BaseRepo[Users]) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "found"
        repo.session.execute = AsyncMock(return_value=mock_result)

        result = await repo.get_by_field("username", "testuser")
        assert result == "found"

    def test_add_calls_session_add(self, repo: BaseRepo[Users]) -> None:
        obj = Mock()
        repo.add(obj)
        repo.session.add.assert_called_once_with(obj)

    async def test_delete_awaits_session_delete(
        self, repo: BaseRepo[Users]
    ) -> None:
        obj = Mock()
        await repo.delete(obj)
        repo.session.delete.assert_awaited_once_with(obj)


class TestUsersRepo:
    def test_init_sets_model(self) -> None:
        session = AsyncMock()
        repo = UsersRepo(session)
        assert repo.model is Users


class TestHardFactsRepo:
    def test_init_sets_model(self) -> None:
        session = AsyncMock()
        repo = HardFactsRepo(session)
        assert repo.model is HardFacts

    async def test_upsert_fact_executes_sql(self) -> None:
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 1
        session.execute = AsyncMock(return_value=mock_result)

        repo = HardFactsRepo(session)
        fact_id = await repo.upsert_fact(
            user_id=uuid.uuid4(),
            category="prefs",
            key="theme",
            value={"color": "dark"},
            confidence=0.9,
        )
        assert fact_id == 1
        session.execute.assert_awaited_once()

    async def test_get_facts_by_uuid(self) -> None:
        session = AsyncMock()
        # session.scalars is async, returns ScalarResult
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = [
            "fact1",
            "fact2",
        ]
        session.scalars = AsyncMock(return_value=mock_scalars_result)

        repo = HardFactsRepo(session)
        facts = await repo.get_facts_by_uuid(uuid.uuid4())
        assert facts == ["fact1", "fact2"]

    async def test_search_last_updated(self) -> None:
        session = AsyncMock()
        mock_scalars_result = MagicMock()
        mock_scalars_result.all.return_value = ["f1"]
        session.scalars = AsyncMock(return_value=mock_scalars_result)

        repo = HardFactsRepo(session)
        facts = await repo.search_last_updated(uuid.uuid4(), limit=5)
        assert facts == ["f1"]


class TestAuditLogsRepo:
    def test_init_sets_model(self) -> None:
        session = AsyncMock()
        repo = AuditLogsRepo(session)
        assert repo.model is AuditLogs
