"""Unit tests for ``app.brain.models``."""

from __future__ import annotations

import uuid

from sqlalchemy import inspect as sa_inspect

from app.brain.models import (
    AuditLogs,
    AuthTokens,
    Base,
    HardFacts,
    Messages,
    Users,
)


class TestBase:
    def test_declarative_base(self) -> None:
        assert hasattr(Base, "metadata")


class TestUsersModel:
    def test_table_name(self) -> None:
        assert Users.__tablename__ == "users"

    def test_has_required_columns(self) -> None:
        mapper = sa_inspect(Users)
        cols = {c.key for c in mapper.columns}
        assert "user_id" in cols
        assert "telegram_id" in cols
        assert "discord_id" in cols
        assert "username" in cols
        assert "passwd_hash" in cols
        assert "role" in cols
        assert "settings" in cols
        assert "created_at" in cols

    def test_user_id_is_pk(self) -> None:
        mapper = sa_inspect(Users)
        pk = [c.key for c in mapper.primary_key]
        assert pk == ["user_id"]

    def test_unique_constraints(self) -> None:
        table = Users.__table__
        uniq_cols = set()
        for u in table.constraints:
            if hasattr(u, "columns"):
                cols = [c.name for c in u.columns]
                if len(cols) == 1:
                    uniq_cols.add(cols[0])
        assert "telegram_id" in uniq_cols
        assert "username" in uniq_cols

    def test_can_instantiate(self) -> None:
        user = Users(
            user_id=uuid.uuid4(),
            telegram_id=12345,
            username="testuser",
            passwd_hash="hash",
            role="user",
        )
        assert user.username == "testuser"


class TestAuthTokensModel:
    def test_table_name(self) -> None:
        assert AuthTokens.__tablename__ == "auth_tokens"

    def test_has_required_columns(self) -> None:
        mapper = sa_inspect(AuthTokens)
        cols = {c.key for c in mapper.columns}
        assert "token_id" in cols
        assert "user_id" in cols
        assert "client_type" in cols
        assert "token_hash" in cols
        assert "created_at" in cols
        assert "revoked_at" in cols


class TestMessagesModel:
    def test_table_name(self) -> None:
        assert Messages.__tablename__ == "messages"

    def test_has_required_columns(self) -> None:
        mapper = sa_inspect(Messages)
        cols = {c.key for c in mapper.columns}
        assert "message_id" in cols
        assert "user_id" in cols
        assert "direction" in cols
        assert "channel" in cols
        assert "text" in cols

    def test_has_user_created_index(self) -> None:
        table = Messages.__table__
        idx_names = {idx.name for idx in table.indexes}
        assert "ix_messages_user_created" in idx_names


class TestHardFactsModel:
    def test_table_name(self) -> None:
        assert HardFacts.__tablename__ == "hard_facts"

    def test_has_required_columns(self) -> None:
        mapper = sa_inspect(HardFacts)
        cols = {c.key for c in mapper.columns}
        assert "fact_id" in cols
        assert "user_id" in cols
        assert "category" in cols
        assert "key" in cols
        assert "value" in cols
        assert "confidence" in cols
        assert "updated_at" in cols

    def test_unique_constraint_exists(self) -> None:
        table = HardFacts.__table__
        constraint_names = {c.name for c in table.constraints if c.name}
        assert "uq_hard_facts_identity" in constraint_names


class TestAuditLogsModel:
    def test_table_name(self) -> None:
        assert AuditLogs.__tablename__ == "audit_logs"

    def test_has_required_columns(self) -> None:
        mapper = sa_inspect(AuditLogs)
        cols = {c.key for c in mapper.columns}
        assert "log_id" in cols
        assert "user_id" in cols
        assert "tool_name" in cols
        assert "arguments" in cols
        assert "status" in cols
