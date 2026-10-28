"""Integration tests for ``app.worker``."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

from app.brain.models import Users
from app.brain.vision import MediaRecognizer
from app.common import IncomingMessage, MediaDescriptor
from app.worker import Worker, _chat_id, _PendingBatch


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
    def test_always_returns_now(self) -> None:
        result = Worker._proactive_decision()
        assert result == "now"


def _make_worker(
    mock_redis: AsyncMock,
    mock_publisher: AsyncMock,
    mock_llm_router: AsyncMock,
    mock_vectors: AsyncMock,
    *,
    recognizer: AsyncMock | None = None,
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


def _make_dto(
    text: str = "смотри",
    media: MediaDescriptor | None = None,
    media_type: str | None = None,
    media_file_id: str | None = None,
) -> IncomingMessage:
    return IncomingMessage(
        user_external_id="111",
        chat_external_id="111",
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
