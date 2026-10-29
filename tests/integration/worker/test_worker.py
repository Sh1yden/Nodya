"""Integration tests for ``app.worker``."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest

from app.brain.feed import (
    AddResult,
    DeactivateResult,
    FeedResolutionError,
    FeedSourceLimitError,
)
from app.brain.models import FeedSources, Users
from app.brain.vision import MediaRecognizer
from app.common import (
    IncomingMessage,
    MediaDescriptor,
    OutgoingMessage,
    ScheduledEnvelope,
)
from app.worker import Worker, _capture_url, _chat_id, _PendingBatch


@pytest.fixture(autouse=True)
def _proactive_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep dialogue tests deterministic: always answer immediately."""
    monkeypatch.setattr("app.worker.settings.PROACTIVE_ENABLED", False)


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

    async def test_resolve_user_refreshes_profile(self) -> None:
        worker = Worker.__new__(Worker)
        object.__setattr__(worker, "_Worker__logger", MagicMock())

        session = AsyncMock()
        object.__setattr__(
            worker,
            "_session_factory",
            _make_session_factory(session),
        )
        user = Users(
            user_id=uuid4(),
            username="tg_111",
            telegram_id=111,
            passwd_hash="x",
            has_usable_password=False,
            role="user",
            settings={},
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = user
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()

        result = await worker._resolve_user(
            "telegram",
            "111",
            sender_name="Иван",
            sender_username="@ivan",
        )

        assert result is user
        assert user.display_name == "Иван"
        assert user.tg_username == "ivan"
        session.commit.assert_awaited_once()
        session.add.assert_not_called()


class TestChatId:
    def test_missing_chat_id(self) -> None:
        dto = IncomingMessage(
            user_external_id="1",
            channel="telegram",
            text="x",
            received_at=datetime.now(UTC),
        )
        assert _chat_id(dto) is None

    def test_group_chat_id(self) -> None:
        dto = IncomingMessage(
            user_external_id="1",
            chat_external_id="-100500",
            channel="telegram",
            text="x",
            received_at=datetime.now(UTC),
        )
        assert _chat_id(dto) == -100500

    def test_malformed_chat_id(self) -> None:
        dto = IncomingMessage(
            user_external_id="1",
            chat_external_id="not-a-number",
            channel="telegram",
            text="x",
            received_at=datetime.now(UTC),
        )
        assert _chat_id(dto) is None


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


class TestWorkerPromptIdentity:
    async def test_system_prompt_names_interlocutor(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        session = AsyncMock()
        facts_result = MagicMock()
        facts_result.all.return_value = []
        session.scalars = AsyncMock(return_value=facts_result)
        session.add_all = MagicMock()
        worker = Worker(
            broker_url="amqp://guest:guest@localhost/",
            redis_client=mock_redis,
            session_factory=_make_session_factory(session),
            publisher=mock_publisher,
            router=mock_llm_router,
            vectors=mock_vectors,
        )
        user = Users(
            user_id=uuid4(),
            username="tg_1188741734",
            telegram_id=1188741734,
            display_name="Иван",
            tg_username="ivan",
            passwd_hash="x",
            has_usable_password=False,
            role="user",
            settings={},
        )
        dto = IncomingMessage(
            user_external_id="1188741734",
            chat_external_id="-100500",
            sender_name="Иван",
            sender_username="ivan",
            channel="telegram",
            text="привет",
            received_at=datetime.now(UTC),
        )

        outgoing = await worker._handle_dtos(user, [dto])

        chat = mock_llm_router.generate_with_fallback.await_args.args[1]
        system = chat[0].content
        assert "Иван" in system
        assert "1188741734" in system
        assert "НЕ твой создатель" in system
        assert outgoing[0].chat_external_id == "-100500"
        mock_publisher.publish_outgoing.assert_awaited_once()


class TestWorkerProactiveDecision:
    def test_disabled_always_now(self) -> None:
        worker = Worker.__new__(Worker)
        assert worker._proactive_decision() == ("now", None)

    @pytest.mark.parametrize("choice", ["now", "skip"])
    def test_now_and_skip_have_no_delay(
        self, monkeypatch: pytest.MonkeyPatch, choice: str
    ) -> None:
        monkeypatch.setattr("app.worker.settings.PROACTIVE_ENABLED", True)
        monkeypatch.setattr(
            "app.worker.random.choices", lambda *a, **k: [choice]
        )
        worker = Worker.__new__(Worker)
        assert worker._proactive_decision() == (choice, None)

    def test_delay_seconds_within_bounds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.worker.settings.PROACTIVE_ENABLED", True)
        monkeypatch.setattr(
            "app.worker.random.choices", lambda *a, **k: ["delay"]
        )
        monkeypatch.setattr(
            "app.worker.random.uniform", lambda low, high: high
        )
        worker = Worker.__new__(Worker)

        action, delay = worker._proactive_decision()

        assert action == "delay"
        assert delay is not None
        assert 300 <= delay <= 7200

    def test_zero_weights_fall_back_to_now(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.worker.settings.PROACTIVE_ENABLED", True)
        monkeypatch.setattr("app.worker.settings.PROACTIVE_NOW_PERCENT", 0)
        monkeypatch.setattr("app.worker.settings.PROACTIVE_DELAY_PERCENT", 0)
        monkeypatch.setattr("app.worker.settings.PROACTIVE_SKIP_PERCENT", 0)
        worker = Worker.__new__(Worker)

        assert worker._proactive_decision() == ("now", None)


class TestWorkerSkipAndDelay:
    async def test_skip_stores_batch_without_llm(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, session = _make_worker(
            mock_redis, mock_publisher, mock_llm_router, mock_vectors
        )
        monkeypatch.setattr(
            worker, "_proactive_decision", lambda: ("skip", None)
        )

        outgoing = await worker._run_locked(_make_user(), [_make_dto()])

        assert outgoing == []
        mock_llm_router.generate_with_fallback.assert_not_awaited()
        mock_publisher.publish_outgoing.assert_not_awaited()
        mock_redis.push_context_many.assert_awaited_once()
        assert session.add_all.call_count == 1

    async def test_delay_schedules_reply_instead_of_publishing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, _ = _make_worker(
            mock_redis, mock_publisher, mock_llm_router, mock_vectors
        )
        monkeypatch.setattr(
            worker, "_proactive_decision", lambda: ("delay", 600.0)
        )

        outgoing = await worker._run_locked(_make_user(), [_make_dto()])

        assert len(outgoing) == 1
        assert outgoing[0].delay_until is not None
        mock_publisher.publish_outgoing.assert_not_awaited()
        mock_redis.push_scheduled.assert_awaited_once()
        member = mock_redis.push_scheduled.await_args.args[0]
        envelope = ScheduledEnvelope.model_validate_json(member)
        assert envelope.kind == "outgoing"
        assert envelope.outgoing is not None
        assert envelope.outgoing.text == "Hello from LLM"


class TestWorkerDeliver:
    async def test_immediate_publish(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, _ = _make_worker(
            mock_redis, mock_publisher, mock_llm_router, mock_vectors
        )
        message = OutgoingMessage(
            user_id=uuid4(), channel="telegram", text="hi"
        )

        await worker._deliver(message)

        mock_publisher.publish_outgoing.assert_awaited_once_with(message)
        mock_redis.push_scheduled.assert_not_awaited()

    async def test_future_message_goes_to_zset(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, _ = _make_worker(
            mock_redis, mock_publisher, mock_llm_router, mock_vectors
        )
        message = OutgoingMessage(
            user_id=uuid4(),
            channel="telegram",
            text="later",
            delay_until=datetime.now(UTC) + timedelta(minutes=5),
        )

        await worker._deliver(message)

        mock_publisher.publish_outgoing.assert_not_awaited()
        mock_redis.push_scheduled.assert_awaited_once()
        member, score = mock_redis.push_scheduled.await_args.args
        envelope = ScheduledEnvelope.model_validate_json(member)
        assert envelope.kind == "outgoing"
        assert envelope.outgoing is not None
        assert envelope.outgoing.text == "later"
        assert score > datetime.now(UTC).timestamp()


def _make_worker(
    mock_redis: AsyncMock,
    mock_publisher: AsyncMock,
    mock_llm_router: AsyncMock,
    mock_vectors: AsyncMock,
    *,
    recognizer: AsyncMock | None = None,
    feed_service: Any = None,
) -> tuple[Worker, AsyncMock]:
    """Build a Worker with a mocked DB session and empty facts."""
    session = AsyncMock()
    facts_result = MagicMock()
    facts_result.all.return_value = []
    session.scalars = AsyncMock(return_value=facts_result)
    session.add_all = MagicMock()
    worker = Worker(
        broker_url="amqp://guest:guest@localhost/",
        redis_client=mock_redis,
        session_factory=_make_session_factory(session),
        publisher=mock_publisher,
        router=mock_llm_router,
        vectors=mock_vectors,
        media_recognizer=recognizer,
        feed_service=feed_service,
    )
    return worker, session


def _make_user() -> Users:
    return Users(
        user_id=uuid4(),
        username="tg_111",
        telegram_id=111,
        display_name="Иван",
        tg_username="ivan",
        passwd_hash="x",
        has_usable_password=False,
        role="user",
        settings={},
    )


def _make_owner() -> Users:
    return Users(
        user_id=uuid4(),
        username="tg_111",
        telegram_id=111,
        display_name="Владелец",
        tg_username="owner",
        passwd_hash="x",
        has_usable_password=False,
        role="owner",
        settings={},
    )


def _source_row(
    *,
    kind: str = "rss",
    title: str = "Feed",
    is_active: bool = True,
) -> FeedSources:
    return FeedSources(
        source_id=uuid4(),
        url="https://example.com/feed.xml",
        kind=kind,  # type: ignore[arg-type]
        title=title,
        is_active=is_active,
    )


def _make_feed_worker(
    mock_redis: AsyncMock,
    mock_publisher: AsyncMock,
    mock_llm_router: AsyncMock,
    mock_vectors: AsyncMock,
    *,
    role: str = "owner",
    service: AsyncMock | None = None,
) -> tuple[Worker, AsyncMock]:
    """Build a Worker whose commands see a fake feed service."""
    svc = service or AsyncMock()
    worker, _ = _make_worker(
        mock_redis,
        mock_publisher,
        mock_llm_router,
        mock_vectors,
        feed_service=svc,
    )
    user = _make_owner() if role == "owner" else _make_user()
    worker._resolve_user = AsyncMock(return_value=user)  # type: ignore[method-assign]
    return worker, svc


def _reply_text(mock_publisher: AsyncMock) -> str:
    return mock_publisher.publish_outgoing.await_args.args[0].text


def _make_dto(
    text: str = "смотри",
    media: MediaDescriptor | None = None,
    media_type: str | None = None,
    media_file_id: str | None = None,
    chat_external_id: str = "111",
) -> IncomingMessage:
    return IncomingMessage(
        user_external_id="111",
        chat_external_id=chat_external_id,
        channel="telegram",
        text=text,
        received_at=datetime.now(UTC),
        media=media,
        media_type=media_type,  # type: ignore[arg-type]
        media_file_id=media_file_id,
    )


def _recognizer_mock(descriptions: list[str | None]) -> AsyncMock:
    recognizer = AsyncMock(spec=MediaRecognizer)
    recognizer.describe_many = AsyncMock(return_value=descriptions)
    return recognizer


class TestWorkerMediaEnrichment:
    async def test_marker_lands_in_prompt_and_context_once(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        recognizer = _recognizer_mock(["[photo: кот]"])
        worker, _ = _make_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            recognizer=recognizer,
        )

        await worker._run_locked(_make_user(), [_make_dto()])

        chat = mock_llm_router.generate_with_fallback.await_args.args[1]
        assert chat[-1].content == "смотри\n[photo: кот]"
        context = mock_redis.push_context_many.await_args.args[1]
        user_messages = [m for m in context if m.role == "user"]
        assert [m.content for m in user_messages] == ["смотри\n[photo: кот]"]
        recognizer.describe_many.assert_awaited_once()

    async def test_recognition_failure_keeps_dialogue(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        recognizer = AsyncMock(spec=MediaRecognizer)
        recognizer.describe_many = AsyncMock(
            side_effect=RuntimeError("vision down")
        )
        worker, _ = _make_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            recognizer=recognizer,
        )

        outgoing = await worker._run_locked(_make_user(), [_make_dto()])

        assert outgoing[0].text == "Hello from LLM"
        mock_publisher.publish_outgoing.assert_awaited_once()
        chat = mock_llm_router.generate_with_fallback.await_args.args[1]
        assert chat[-1].content == "смотри"

    async def test_handle_dtos_enriches_when_not_prepared(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        recognizer = _recognizer_mock(["[gif: кот танцует; 3s]"])
        worker, _ = _make_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            recognizer=recognizer,
        )

        await worker._handle_dtos(
            _make_user(),
            [_make_dto(media=MediaDescriptor(kind="animation", file_id="g"))],
        )

        chat = mock_llm_router.generate_with_fallback.await_args.args[1]
        assert chat[-1].content == "смотри\n[gif: кот танцует; 3s]"
        recognizer.describe_many.assert_awaited_once()

    async def test_worker_without_recognizer_keeps_raw_text(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, _ = _make_worker(
            mock_redis, mock_publisher, mock_llm_router, mock_vectors
        )

        await worker._run_locked(_make_user(), [_make_dto(text="привет")])

        chat = mock_llm_router.generate_with_fallback.await_args.args[1]
        assert chat[-1].content == "привет"
        context = mock_redis.push_context_many.await_args.args[1]
        assert [m.content for m in context if m.role == "user"] == ["привет"]

    async def test_second_turn_sees_media_description(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        recognizer = _recognizer_mock(["[photo: кот]"])
        worker, _ = _make_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            recognizer=recognizer,
        )
        user = _make_user()

        await worker._run_locked(user, [_make_dto()])
        mock_redis.get_context.return_value = (
            mock_redis.push_context_many.await_args.args[1]
        )

        await worker._handle_dtos(
            user,
            [_make_dto(text="что на фото?")],
            enriched_texts=["что на фото?"],
        )

        chat = mock_llm_router.generate_with_fallback.await_args.args[1]
        history = [m.content for m in chat if isinstance(m.content, str)]
        assert any("[photo: кот]" in content for content in history)


class TestWorkerArchive:
    async def test_archive_uses_enriched_text_and_descriptor_kind(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, session = _make_worker(
            mock_redis, mock_publisher, mock_llm_router, mock_vectors
        )
        dto = _make_dto(
            text="подпись",
            media=MediaDescriptor(kind="animation", file_id="gif"),
        )

        await worker._archive_batch(
            uuid4(),
            incoming=[dto],
            incoming_texts=["подпись\n[gif: кот]"],
            outgoing=[],
        )

        rows = session.add_all.call_args.args[0]
        assert len(rows) == 1
        assert rows[0].text == "подпись\n[gif: кот]"
        assert rows[0].source_kind == "animation"

    async def test_archive_falls_back_to_legacy_kind(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, session = _make_worker(
            mock_redis, mock_publisher, mock_llm_router, mock_vectors
        )
        dto = _make_dto(media_type="photo", media_file_id="file-1")

        await worker._archive_batch(
            uuid4(), incoming=[dto], incoming_texts=["x"], outgoing=[]
        )

        rows = session.add_all.call_args.args[0]
        assert rows[0].source_kind == "photo"

    async def test_archive_text_only_kind(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, session = _make_worker(
            mock_redis, mock_publisher, mock_llm_router, mock_vectors
        )

        await worker._archive_batch(
            uuid4(),
            incoming=[_make_dto(text="привет")],
            incoming_texts=["привет"],
            outgoing=[],
        )

        rows = session.add_all.call_args.args[0]
        assert rows[0].source_kind == "text"


class TestFeedCommandPattern:
    def test_add_is_parsed(self) -> None:
        from app.worker import _FEED_COMMAND_PATTERN

        match = _FEED_COMMAND_PATTERN.match("/feed add https://t.me/s/chan")
        assert match is not None
        assert match.group(1) == "add"
        assert match.group(2) == "https://t.me/s/chan"

    def test_bot_suffix_is_allowed(self) -> None:
        from app.worker import _FEED_COMMAND_PATTERN

        match = _FEED_COMMAND_PATTERN.match("/feed@nodya_bot list")
        assert match is not None
        assert match.group(1) == "list"

    def test_bare_feed_is_allowed(self) -> None:
        from app.worker import _FEED_COMMAND_PATTERN

        match = _FEED_COMMAND_PATTERN.match("/feed")
        assert match is not None
        assert match.group(1) is None

    def test_link_command_is_not_feed(self) -> None:
        from app.worker import _FEED_COMMAND_PATTERN

        assert _FEED_COMMAND_PATTERN.match("/link ABCD1234") is None


class TestFeedCommands:
    async def test_non_owner_is_rejected(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        worker, service = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            role="user",
        )
        message = amqp_message_factory("{}")

        handled = await worker._try_command(
            _make_dto(text="/feed list"), message
        )

        assert handled is True
        service.list.assert_not_awaited()
        assert _reply_text(mock_publisher) == (
            "Эта команда доступна только владельцу."
        )
        message.ack.assert_awaited_once()

    async def test_owner_add_confirms_source(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        service = AsyncMock()
        service.add = AsyncMock(
            return_value=AddResult(
                source=_source_row(kind="telegram", title="Канал"),
                status="added",
            )
        )
        worker, service = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )
        message = amqp_message_factory("{}")

        await worker._try_command(
            _make_dto(text="/feed add https://t.me/s/chan"), message
        )

        service.add.assert_awaited_once_with("https://t.me/s/chan")
        text = _reply_text(mock_publisher)
        assert text.startswith("Добавила: Канал · Telegram")
        assert "https://example.com/feed.xml" in text

    async def test_owner_list_shows_disabled(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        active = _source_row()
        disabled = _source_row(title="Старый", is_active=False)
        service = AsyncMock()
        service.list = AsyncMock(return_value=[active, disabled])
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )
        message = amqp_message_factory("{}")

        await worker._try_command(_make_dto(text="/feed list"), message)

        text = _reply_text(mock_publisher)
        assert "Источники (2):" in text
        assert active.source_id.hex[:8] in text
        assert "(выкл.)" in text

    async def test_owner_rm_disables_source(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        row = _source_row()
        service = AsyncMock()
        service.deactivate = AsyncMock(
            return_value=DeactivateResult(source=row, changed=True)
        )
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )
        message = amqp_message_factory("{}")
        ref = row.source_id.hex[:8]

        await worker._try_command(_make_dto(text=f"/feed rm {ref}"), message)

        service.deactivate.assert_awaited_once_with(ref)
        assert _reply_text(mock_publisher) == ("Источник выключен: Feed.")

    async def test_owner_rm_unknown_source(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        service = AsyncMock()
        service.deactivate = AsyncMock(return_value=None)
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )

        await worker._try_command(
            _make_dto(text="/feed rm deadbeef"), amqp_message_factory("{}")
        )

        assert _reply_text(mock_publisher) == "Источник не найден."

    async def test_resolution_error_is_reported(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        service = AsyncMock()
        service.add = AsyncMock(
            side_effect=FeedResolutionError("Не нашёл RSS/Atom-ленту.")
        )
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )

        await worker._try_command(
            _make_dto(text="/feed add https://site.example/"),
            amqp_message_factory("{}"),
        )

        assert _reply_text(mock_publisher) == (
            "Не смогла добавить источник: Не нашёл RSS/Atom-ленту."
        )

    async def test_source_cap_error_is_reported(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        service = AsyncMock()
        service.add = AsyncMock(
            side_effect=FeedSourceLimitError(
                "Достигнут лимит активных источников (20)."
            )
        )
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )

        await worker._try_command(
            _make_dto(text="/feed add https://t.me/s/chan"),
            amqp_message_factory("{}"),
        )

        assert "лимит активных источников" in _reply_text(mock_publisher)

    async def test_group_chat_is_rejected(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        worker, service = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
        )

        await worker._try_command(
            _make_dto(text="/feed list", chat_external_id="-100500"),
            amqp_message_factory("{}"),
        )

        service.list.assert_not_awaited()
        assert _reply_text(mock_publisher) == (
            "Команды /feed доступны только в личном чате."
        )

    async def test_bare_feed_shows_usage(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
        amqp_message_factory: Any,
    ) -> None:
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
        )

        await worker._try_command(
            _make_dto(text="/feed"), amqp_message_factory("{}")
        )

        assert "Использование" in _reply_text(mock_publisher)


class TestCaptureUrl:
    def test_telegram_link(self) -> None:
        assert _capture_url("https://t.me/s/chan") == ("https://t.me/s/chan")

    def test_bare_telegram_host(self) -> None:
        assert _capture_url("t.me/chan") == "t.me/chan"

    def test_youtube_channel(self) -> None:
        url = "https://www.youtube.com/@chan"
        assert _capture_url(url) == url

    def test_feed_url(self) -> None:
        url = "https://example.com/rss.xml"
        assert _capture_url(url) == url

    def test_query_feed_is_captured(self) -> None:
        url = "https://example.com/?feed=rss2"
        assert _capture_url(url) == url

    def test_plain_site_is_rejected(self) -> None:
        assert _capture_url("https://example.com/about") is None

    def test_link_inside_prose_is_rejected(self) -> None:
        assert _capture_url("смотри https://t.me/chan") is None

    def test_surrounding_brackets_are_stripped(self) -> None:
        assert _capture_url("(https://t.me/chan).") == ("https://t.me/chan")


class TestAutoCapture:
    async def test_owner_link_is_added(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        service = AsyncMock()
        service.add = AsyncMock(
            return_value=AddResult(
                source=_source_row(kind="telegram", title="Канал"),
                status="added",
            )
        )
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )

        await worker._capture_source(
            _make_dto(text="https://t.me/chan"), "https://t.me/chan"
        )

        mock_redis.consume_daily_quota.assert_awaited_once()
        service.add.assert_awaited_once_with("https://t.me/chan")
        assert "Добавила: Канал · Telegram" in _reply_text(mock_publisher)

    async def test_non_owner_link_is_ignored(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, service = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            role="user",
        )

        await worker._capture_source(
            _make_dto(text="https://t.me/chan"), "https://t.me/chan"
        )

        mock_redis.consume_daily_quota.assert_not_awaited()
        service.add.assert_not_awaited()
        mock_publisher.publish_outgoing.assert_not_awaited()

    async def test_daily_capture_quota_is_enforced(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        mock_redis.consume_daily_quota = AsyncMock(return_value=False)
        worker, service = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
        )

        await worker._capture_source(
            _make_dto(text="https://t.me/chan"), "https://t.me/chan"
        )

        service.add.assert_not_awaited()
        assert "лимит авто-добавления" in _reply_text(mock_publisher)

    async def test_capture_resolution_error_is_reported(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        service = AsyncMock()
        service.add = AsyncMock(
            side_effect=FeedResolutionError("Канал не найден.")
        )
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )

        await worker._capture_source(
            _make_dto(text="https://t.me/chan"), "https://t.me/chan"
        )

        assert _reply_text(mock_publisher) == (
            "Не смогла добавить источник: Канал не найден."
        )

    async def test_duplicate_link_schedules_one_task(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        service = AsyncMock()
        service.add = AsyncMock(
            return_value=AddResult(source=_source_row(), status="added")
        )
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
            service=service,
        )
        dto = _make_dto(text="https://t.me/chan")

        worker._maybe_capture_source(dto)
        worker._maybe_capture_source(dto)
        await asyncio.gather(*worker._capture_tasks)

        assert len(worker._captured_refs) == 1
        service.add.assert_awaited_once()

    async def test_link_inside_prose_is_not_scheduled(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, service = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
        )

        worker._maybe_capture_source(
            _make_dto(text="смотри https://t.me/chan")
        )

        assert worker._captured_refs == set()
        assert worker._capture_tasks == set()
        service.add.assert_not_awaited()

    async def test_group_chat_link_is_not_scheduled(
        self,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
        )

        worker._maybe_capture_source(
            _make_dto(text="https://t.me/chan", chat_external_id="-100500")
        )

        assert worker._captured_refs == set()

    async def test_disabled_capture_ignores_links(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_redis: AsyncMock,
        mock_publisher: AsyncMock,
        mock_llm_router: AsyncMock,
        mock_vectors: AsyncMock,
    ) -> None:
        monkeypatch.setattr(
            "app.worker.settings.FEED_AUTOCAPTURE_ENABLED", False
        )
        worker, _ = _make_feed_worker(
            mock_redis,
            mock_publisher,
            mock_llm_router,
            mock_vectors,
        )

        worker._maybe_capture_source(_make_dto(text="https://t.me/chan"))

        assert worker._captured_refs == set()
