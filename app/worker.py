"""Worker: consumer of the incoming_messages queue.

Full batch cycle (ADR-12/15):
1. Debounce: fixed DEBOUNCE_SECONDS window from the first message;
   the batch accumulates in process memory, AMQP messages stay
   unacked until processing.
2. resolve_user: lookup / auto-registration by channel external id.
3. User lock; when busy -> batch into nodya:scheduled (+30s),
   retry_count >= MAX_SCHEDULED_RETRIES -> DLQ.
4. Processing: LLM dialogue via LLMRouter (fallback chains).
5. Publish OutgoingMessage(s) and ACK the whole batch.

A separate poller moves matured nodya:scheduled entries back into
processing every SCHEDULED_POLL_SECONDS (ADR-13).
"""

import asyncio
import random
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import aio_pika
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustQueue,
)
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from app.api.messaging import MessagePublisher
from app.brain import build_interlocutor_block, load_system_prompt
from app.brain.feed import (
    AddResult,
    FeedResolutionError,
    FeedSourceLimitError,
    FeedSourceService,
)
from app.brain.llm_choice import ChatMessage, LLMRouter
from app.brain.memory.short import ContextMessage, RedisClient
from app.brain.memory.vector import VectorMemory
from app.brain.models import FeedSources, HardFacts, Messages, Users
from app.brain.repositories import HardFactsRepo, UsersRepo
from app.brain.vision import MediaRecognizer
from app.common import (
    IncomingMessage,
    OutgoingMessage,
    ScheduledEnvelope,
    TypingEvent,
    declare_incoming_queue,
    declare_topology,
)
from app.core import LoggerMixin, settings

_PREFETCH_COUNT = 50
_THINKING_TTL_SECONDS = 300
_BATCH_LOCK_TTL_SECONDS = 180
_LINK_COMMAND_PATTERN = re.compile(
    r"^/(?:link|start)\s+([A-Za-z0-9]{6,12})$", re.IGNORECASE
)
_FEED_COMMAND_PATTERN = re.compile(
    r"^/feed(?:@[A-Za-z0-9_]+)?(?:\s+(add|list|rm)(?:\s+(.+))?)?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_LINK_ONLY_PATTERN = re.compile(
    r"^[<(\[]?\s*((?:https?://)?[\w.-]+\.[a-z]{2,}(?:/\S*)?)\s*[>)\].!?,;:]*$",
    re.IGNORECASE,
)
_FEED_USAGE = "Использование: /feed add <url> | /feed list | /feed rm <id|url>"
_FEED_RSS_HINTS = (".xml", ".rss", ".atom", "/feed", "/rss", "/feeds")
_FEED_LIST_LIMIT = 30
_HISTORY_LIMIT_FALLBACK = 20


def _chat_id(dto: IncomingMessage) -> int | None:
    """Parse the delivery chat id from a channel DTO."""
    if dto.chat_external_id is None:
        return None
    try:
        return int(dto.chat_external_id)
    except ValueError:
        return None


def _capture_url(text: str) -> str | None:
    """Extract a feed-like URL from a bare-link message.

    Args:
        text: Raw message text.

    Returns:
        The captured URL, or None when the message is not a bare
        channel/feed link.
    """
    match = _LINK_ONLY_PATTERN.match(text.strip())
    if match is None:
        return None
    raw = match.group(1).rstrip(".,!?;:)]}>")
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    if host in ("t.me", "telegram.me"):
        return raw
    if host in ("youtube.com", "m.youtube.com"):
        if path.startswith(("/@", "/channel/", "/c/", "/user/")):
            return raw
        return None
    if any(hint in path for hint in _FEED_RSS_HINTS):
        return raw
    if "feed=" in parsed.query.lower() or "rss" in parsed.query.lower():
        return raw
    return None


def _source_title(source: FeedSources) -> str:
    """Display title of a source row."""
    return (source.title or source.url)[:64]


def _kind_label(kind: str) -> str:
    """Human-readable source kind."""
    return "Telegram" if kind == "telegram" else "RSS"


def _add_result_text(result: AddResult) -> str:
    """Format the outcome of adding a source."""
    source = result.source
    if result.status == "exists":
        return "Этот источник уже отслеживается."
    if result.status == "reactivated":
        return f"Источник снова включён: {_source_title(source)}"
    return (
        f"Добавила: {_source_title(source)} · "
        f"{_kind_label(source.kind)}\n{source.url}"
    )


def _format_sources(sources: list[FeedSources]) -> str:
    """Format the source list for a chat reply."""
    if not sources:
        return "Пока нет источников. Добавь: /feed add <url>"
    lines = [f"Источники ({len(sources)}):"]
    for index, source in enumerate(sources[:_FEED_LIST_LIMIT], start=1):
        short_id = source.source_id.hex[:8]
        suffix = "" if source.is_active else " (выкл.)"
        lines.append(
            f"{index}. {short_id} · "
            f"{_kind_label(source.kind)} · "
            f"{_source_title(source)}{suffix}"
        )
    return "\n".join(lines)


@dataclass(slots=True)
class _PendingBatch:
    """Batch inside the debounce buffer: AMQP envelopes plus DTOs."""

    amqp_messages: list[AbstractIncomingMessage] = field(default_factory=list)
    dtos: list[IncomingMessage] = field(default_factory=list)


class Worker(LoggerMixin):
    """Processor of incoming messages (dialogue role)."""

    def __init__(
        self,
        broker_url: str,
        redis_client: RedisClient,
        session_factory: type,
        publisher: MessagePublisher,
        router: LLMRouter,
        vectors: VectorMemory,
        media_recognizer: MediaRecognizer | None = None,
        feed_service: FeedSourceService | None = None,
    ) -> None:
        """Store collaborators; no I/O happens here.

        Args:
            broker_url: AMQP DSN for the incoming consumer.
            redis_client: Short-term memory client (locks/scheduled).
            session_factory: Async session factory for DB access.
            publisher: Outgoing publisher into RabbitMQ.
            router: LLM router used for dialogue generation.
            vectors: Qdrant-backed long-term fact memory.
            media_recognizer: Optional vision describer; None
                disables media enrichment.
            feed_service: Optional source management service (tests);
                a default one is built from the session factory.
        """
        self._url = broker_url
        self._redis = redis_client
        self._session_factory = session_factory
        self._publisher = publisher
        self._router = router
        self._vectors = vectors
        self._media_recognizer = media_recognizer
        self._feed_service = feed_service or FeedSourceService(session_factory)
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._queue: AbstractRobustQueue | None = None
        self._running = False
        self._poller_task: asyncio.Task | None = None
        self._buffers: dict[str, _PendingBatch] = {}
        self._timers: dict[str, asyncio.Task] = {}
        self._capture_tasks: set[asyncio.Task] = set()
        self._captured_refs: set[tuple[str, str]] = set()

    async def run(self) -> None:
        """Connect to the broker and start consuming incoming."""
        self._lg.debug("Worker starting...")
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=_PREFETCH_COUNT)
        exchange = await declare_topology(self._channel)
        self._queue = await declare_incoming_queue(self._channel, exchange)
        self._running = True
        self._poller_task = asyncio.create_task(
            self._scheduled_poller(), name="worker-scheduled-poller"
        )
        await self._queue.consume(self._on_message)

    async def stop(self) -> None:
        """Graceful shutdown; unacked messages return to the queue."""
        self._running = False
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        self._buffers.clear()
        if self._poller_task is not None:
            self._poller_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poller_task
            self._poller_task = None
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
        self._lg.debug("Worker stopped.")

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        """Accept a delivery and place it into the user debounce buffer.

        Args:
            message: Raw AMQP delivery from incoming_messages.
        """
        try:
            dto = IncomingMessage.model_validate_json(message.body)
        except ValueError:
            logger_payload = "Malformed payload in incoming queue."
            self._lg.warning(logger_payload)
            await message.nack(requeue=False)
            return

        key = dto.user_external_id
        self._lg.debug(
            f"Message received: user={key} channel={dto.channel} "
            f"len={len(dto.text)}"
        )
        if await self._try_command(dto, message):
            return
        self._maybe_capture_source(dto)
        batch = self._buffers.get(key)
        if batch is None:
            batch = _PendingBatch()
            self._buffers[key] = batch
            self._timers[key] = asyncio.create_task(
                self._flush_after_debounce(key),
                name=f"debounce:{key}",
            )
        batch.amqp_messages.append(message)
        batch.dtos.append(dto)

    async def _try_command(
        self,
        dto: IncomingMessage,
        message: AbstractIncomingMessage,
    ) -> bool:
        """Intercept pairing and feed commands before the dialogue.

        Commands bypass debounce/state/LLM/archive completely: they
        are acknowledged immediately after handling so the memory of
        the conversation stays clean.

        Args:
            dto: Parsed incoming message.
            message: Raw AMQP delivery to acknowledge.

        Returns:
            True when the message was a handled command.
        """
        text = dto.text.strip()
        match = _LINK_COMMAND_PATTERN.match(text)
        if match is not None:
            if not self._is_private_chat(dto):
                await self._ack_with_reply(
                    dto,
                    message,
                    "Линковка аккаунта доступна только в личном чате.",
                )
                return True
            try:
                await self._handle_link(dto, message, match.group(1))
            except Exception as exc:
                self._lg.error(f"/link handling failed: {exc}")
                await self._ack_with_reply(
                    dto, message, "Не смог связать аккаунт — попробуй позже."
                )
            return True
        feed_match = _FEED_COMMAND_PATTERN.match(text)
        if feed_match is None:
            return False
        if not self._is_private_chat(dto):
            await self._ack_with_reply(
                dto,
                message,
                "Команды /feed доступны только в личном чате.",
            )
            return True
        try:
            await self._handle_feed_command(dto, message, feed_match)
        except Exception as exc:
            self._lg.error(f"/feed handling failed: {exc}")
            await self._ack_with_reply(
                dto,
                message,
                "Не смогла обработать команду — попробуй позже.",
            )
        return True

    @staticmethod
    def _is_private_chat(dto: IncomingMessage) -> bool:
        """Whether the message comes from the user's direct chat."""
        return (
            dto.chat_external_id is None
            or dto.chat_external_id == dto.user_external_id
        )

    async def _handle_feed_command(
        self,
        dto: IncomingMessage,
        message: AbstractIncomingMessage,
        match: re.Match[str],
    ) -> None:
        """Handle /feed add|list|rm for the owner account.

        Args:
            dto: The command message.
            message: Raw AMQP delivery to acknowledge.
            match: Parsed /feed command parts.
        """
        user = await self._resolve_user(
            dto.channel,
            dto.user_external_id,
            sender_name=dto.sender_name,
            sender_username=dto.sender_username,
        )
        if user.role != "owner":
            await self._ack_with_reply(
                dto, message, "Эта команда доступна только владельцу."
            )
            return
        action = (match.group(1) or "").lower()
        argument = (match.group(2) or "").strip()
        if action == "add":
            if not argument:
                await self._ack_with_reply(dto, message, _FEED_USAGE)
                return
            try:
                result = await self._feed_service.add(argument)
            except (FeedResolutionError, FeedSourceLimitError) as exc:
                await self._ack_with_reply(
                    dto, message, f"Не смогла добавить источник: {exc}"
                )
                return
            await self._ack_with_reply(dto, message, _add_result_text(result))
            return
        if action == "list":
            sources = await self._feed_service.list()
            await self._ack_with_reply(dto, message, _format_sources(sources))
            return
        if action == "rm":
            if not argument:
                await self._ack_with_reply(dto, message, _FEED_USAGE)
                return
            removed = await self._feed_service.deactivate(argument)
            if removed is None:
                reply = "Источник не найден."
            elif removed.changed:
                reply = f"Источник выключен: {_source_title(removed.source)}."
            else:
                reply = (
                    f"Источник уже выключен: {_source_title(removed.source)}."
                )
            await self._ack_with_reply(dto, message, reply)
            return
        await self._ack_with_reply(dto, message, _FEED_USAGE)

    def _maybe_capture_source(self, dto: IncomingMessage) -> None:
        """Schedule auto-capture when the owner sends a bare feed link.

        The message itself is not consumed: it still reaches the
        dialogue batch. Only channel links and obvious feed URLs are
        captured, so ordinary links in prose are ignored.

        Args:
            dto: Incoming message under consideration.
        """
        if not settings.FEED_AUTOCAPTURE_ENABLED:
            return
        if not self._is_private_chat(dto):
            return
        raw = _capture_url(dto.text)
        if raw is None:
            return
        key = f"{dto.channel}:{dto.user_external_id}"
        ref = (key, raw)
        if ref in self._captured_refs:
            return
        self._captured_refs.add(ref)
        task = asyncio.create_task(self._capture_source(dto, raw))
        self._capture_tasks.add(task)
        task.add_done_callback(self._capture_tasks.discard)

    async def _capture_source(self, dto: IncomingMessage, raw: str) -> None:
        """Add a source from a bare link in the background.

        Owner-only; never raises into the consumer loop.

        Args:
            dto: Message that contained the link.
            raw: Normalized-enough URL captured from the message.
        """
        try:
            user = await self._resolve_user(
                dto.channel,
                dto.user_external_id,
                sender_name=dto.sender_name,
                sender_username=dto.sender_username,
            )
        except Exception as exc:
            self._lg.warning(f"Feed auto-capture resolve failed: {exc}")
            return
        try:
            if user.role != "owner":
                return
            allowed = await self._redis.consume_daily_quota(
                f"feed_capture:{user.user_id}",
                settings.FEED_AUTOCAPTURE_DAILY_LIMIT,
            )
            if not allowed:
                await self._publish_text(
                    dto,
                    user,
                    "Дневной лимит авто-добавления исчерпан — "
                    "используй /feed add <url>.",
                )
                return
            result = await self._feed_service.add(raw)
            await self._publish_text(dto, user, _add_result_text(result))
            self._lg.debug(f"Feed auto-captured: {raw}")
        except (FeedResolutionError, FeedSourceLimitError) as exc:
            await self._publish_text(
                dto, user, f"Не смогла добавить источник: {exc}"
            )
        except Exception as exc:
            self._lg.warning(f"Feed auto-capture failed ({raw}): {exc}")

    async def _publish_text(
        self, dto: IncomingMessage, user: Users, text: str
    ) -> None:
        """Send a chat message without acknowledging the delivery.

        Args:
            dto: Source message (provides channel and chat id).
            user: Resolved recipient account.
            text: Human-facing reply text.
        """
        await self._publisher.publish_outgoing(
            OutgoingMessage(
                user_id=user.user_id,
                channel=dto.channel,
                text=text,
                chat_external_id=dto.chat_external_id,
            )
        )

    async def _handle_link(
        self,
        dto: IncomingMessage,
        message: AbstractIncomingMessage,
        code: str,
    ) -> None:
        """Pair a Telegram account with an authenticated one (L1).

        Consumes the one-time code, binds telegram_id onto the target
        account and, when an auto-registered duplicate already holds
        this telegram id, migrates its memory (messages, facts,
        Redis keys, Qdrant labels) before removing it.

        Args:
            dto: The /link message itself.
            message: Raw AMQP delivery to acknowledge.
            code: Pairing code extracted from the text.
        """
        target_id = await self._redis.consume_link_code(code)
        if target_id is None:
            self._lg.warning("Link rejected: unknown or expired code.")
            await self._ack_with_reply(
                dto, message, "Код недействителен или истёк."
            )
            return

        telegram_id = int(dto.user_external_id)
        async with self._session_factory() as session:
            repo = UsersRepo(session)
            target = await repo.get_by_id(target_id)
            if target is None:
                await self._ack_with_reply(
                    dto,
                    message,
                    "Аккаунт из кода не найден — зарегистрируйся заново.",
                )
                return
            duplicate = await repo.get_by_field("telegram_id", telegram_id)

            if duplicate is not None and duplicate.user_id == target.user_id:
                await self._ack_with_reply(
                    dto, message, "Этот Telegram уже привязан к аккаунту."
                )
                return
            if (
                target.telegram_id is not None
                and target.telegram_id != telegram_id
            ):
                await self._ack_with_reply(
                    dto,
                    message,
                    "К этому аккаунту уже привязан другой Telegram.",
                )
                return

            merged = duplicate is not None and (
                duplicate.user_id != target.user_id
            )
            if merged:
                assert duplicate is not None
                old_user_id = duplicate.user_id
                # Core statements in explicit order: unique(telegram_id)
                # must be freed on the duplicate BEFORE the target takes it.
                for stmt in (
                    sa_update(Messages)
                    .where(Messages.user_id == old_user_id)
                    .values(user_id=target.user_id),
                    sa_update(HardFacts)
                    .where(HardFacts.user_id == old_user_id)
                    .values(user_id=target.user_id),
                    sa_update(Users)
                    .where(Users.user_id == old_user_id)
                    .values(telegram_id=None),
                    sa_delete(Users).where(Users.user_id == old_user_id),
                    sa_update(Users)
                    .where(Users.user_id == target.user_id)
                    .values(telegram_id=telegram_id),
                ):
                    await session.execute(stmt)
            else:
                target.telegram_id = telegram_id
            await session.commit()

        # Post-commit side effects run independently: a failure in
        # one storage must not hide the success of the other, and the
        # user gets an honest report about partial transfer.
        issues: list[str] = []
        if merged:
            try:
                await self._redis.rename_user_keys(old_user_id, target.user_id)
            except Exception as exc:
                issues.append(f"Redis keys: {exc}")
            try:
                await self._vectors.reassign_user(old_user_id, target.user_id)
            except Exception as exc:
                issues.append(f"Qdrant labels: {exc}")

        reply = "Аккаунт связан ✓"
        if merged:
            reply += " Память перенесена сюда."
        if issues:
            reply += " Часть переноса не завершилась — сообщи владельцу."
            self._lg.error(f"Link post-merge issues: {issues}")
        self._lg.info(
            f"Telegram linked: tg={telegram_id} -> "
            f"user_id={target.user_id} (merged={merged})."
        )
        await self._ack_with_reply(dto, message, reply)

    async def _ack_with_reply(
        self,
        dto: IncomingMessage,
        message: AbstractIncomingMessage,
        text: str,
    ) -> None:
        """Publish a direct chat reply and acknowledge the delivery.

        Args:
            dto: Source command message (provides the channel).
            message: Raw AMQP delivery to acknowledge.
            text: Human-facing reply text.
        """
        local_user = await self._resolve_user(
            "telegram",
            dto.user_external_id,
            sender_name=dto.sender_name,
            sender_username=dto.sender_username,
        )
        await self._publisher.publish_outgoing(
            OutgoingMessage(
                user_id=local_user.user_id,
                channel=dto.channel,
                text=text,
                chat_external_id=dto.chat_external_id,
            )
        )
        await message.ack()

    async def _flush_after_debounce(self, key: str) -> None:
        """Wait out the silence window, then process the batch.

        Args:
            key: External user id owning the buffer.
        """
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(settings.DEBOUNCE_SECONDS)
        batch = self._buffers.pop(key, None)
        self._timers.pop(key, None)
        self._captured_refs = {
            ref for ref in self._captured_refs if ref[0] != key
        }
        if batch is None or not self._running:
            return
        self._lg.debug(f"Debounce finished: batch n={len(batch.dtos)}")
        try:
            await self._process_batch(batch.dtos, batch.amqp_messages)
        except Exception as exc:
            self._lg.error(f"Batch failed for user={key}: {exc}")
            for amqp_message in batch.amqp_messages:
                await amqp_message.nack(requeue=False)

    async def _process_batch(
        self,
        dtos: list[IncomingMessage],
        amqp_messages: list[AbstractIncomingMessage],
    ) -> None:
        """Resolve user, take the lock, handle, publish, ACK.

        Args:
            dtos: Parsed incoming messages of the batch.
            amqp_messages: Matching raw deliveries to acknowledge.
        """
        first = dtos[0]
        user = await self._resolve_user(
            first.channel,
            first.user_external_id,
            sender_name=first.sender_name,
            sender_username=first.sender_username,
        )
        lock_token = await self._redis.acquire_lock(
            user.user_id, ttl=_BATCH_LOCK_TTL_SECONDS
        )
        if lock_token is None:
            self._lg.warning(
                f"Lock busy for user_id={user.user_id}, postponing "
                f"batch by {settings.SCHEDULED_POLL_SECONDS}s."
            )
            await self._defer_incoming(dtos, amqp_messages, retry=0)
            return
        # Typing indicator (ADR-17): publish start/stop via RabbitMQ,
        # consumed by channel senders; failures must not break batch.
        chat_id = _chat_id(first)
        await self._publish_typing(
            user.user_id, first.channel, "start", chat_id
        )
        try:
            await self._run_locked(user, dtos)
        finally:
            await self._publish_typing(
                user.user_id, first.channel, "stop", chat_id
            )
            await self._redis.release_lock(user.user_id, lock_token)
        self._lg.debug(
            f"Batch processed: n={len(dtos)} user_id={user.user_id}"
        )
        for amqp_message in amqp_messages:
            await amqp_message.ack()

    async def _publish_typing(
        self,
        user_id: UUID,
        channel: str,
        action: str,
        chat_id: int | None = None,
    ) -> None:
        """Publish typing event, suppressing errors.

        Args:
            user_id: Internal user UUID.
            channel: Channel name.
            action: ``start`` or ``stop``.
            chat_id: Destination chat when it differs from the user's
                direct chat (groups).
        """
        try:
            await self._publisher.publish_typing(
                TypingEvent(
                    user_id=user_id,
                    channel=channel,
                    action=action,
                    chat_id=chat_id,
                )
            )
        except Exception as exc:
            self._lg.warning(f"Typing {action} failed for {user_id}: {exc}")

    async def _run_locked(
        self, user: Users, dtos: list[IncomingMessage]
    ) -> list[OutgoingMessage]:
        """Handle a batch under an already-taken lock.

        Sets thinking with a TTL (a crashed worker releases the state
        automatically), applies the proactive decision, generates the
        reply, saves context and returns to idle.

        Args:
            user: Interlocutor owning the dialogue.
            dtos: Batch messages to answer.

        Returns:
            Published (or scheduled) outgoing messages.
        """
        user_id = user.user_id
        action, delay_seconds = self._proactive_decision()
        await self._redis.set_state(
            user_id, "thinking", ttl=_THINKING_TTL_SECONDS
        )
        self._lg.debug(f"State thinking: user_id={user_id}")
        enriched = await self._enrich_dtos(dtos)
        if action == "skip":
            # The batch is remembered but left unanswered on purpose
            # (10% branch of the proactive decision).
            self._lg.debug(f"Proactive skip: user_id={user_id}")
            await self._save_dialog_context(user_id, enriched, [])
            await self._archive_batch(
                user_id,
                incoming=dtos,
                incoming_texts=enriched,
                outgoing=[],
            )
            await self._redis.set_state(user_id, "idle")
            self._lg.debug(f"State idle: user_id={user_id}")
            return []
        delay_until: datetime | None = None
        if action == "delay" and delay_seconds is not None:
            delay_until = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            self._lg.debug(
                f"Proactive delay: user_id={user_id} by {delay_seconds:.0f}s"
            )
        outgoing = await self._handle_dtos(
            user,
            dtos,
            enriched_texts=enriched,
            delay_until=delay_until,
        )
        await self._save_dialog_context(user_id, enriched, outgoing)
        await self._redis.set_state(user_id, "idle")
        self._lg.debug(f"State idle: user_id={user_id}")
        return outgoing

    def _proactive_decision(
        self,
    ) -> tuple[Literal["now", "delay", "skip"], float | None]:
        """Decide how to react to a batch (stage 4.2).

        70% answer now, 20% generate now but delay delivery by a
        random 5 min..2 h, 10% skip the answer entirely. All weights
        and bounds are configurable.

        Returns:
            Tuple of action and delay seconds (only for "delay").
        """
        if not settings.PROACTIVE_ENABLED:
            return "now", None
        weights = [
            max(0, settings.PROACTIVE_NOW_PERCENT),
            max(0, settings.PROACTIVE_DELAY_PERCENT),
            max(0, settings.PROACTIVE_SKIP_PERCENT),
        ]
        if sum(weights) <= 0:
            return "now", None
        action = random.choices(
            ["now", "delay", "skip"], weights=weights, k=1
        )[0]
        if action != "delay":
            return action, None
        low = min(
            settings.PROACTIVE_DELAY_MIN_SECONDS,
            settings.PROACTIVE_DELAY_MAX_SECONDS,
        )
        high = max(
            settings.PROACTIVE_DELAY_MIN_SECONDS,
            settings.PROACTIVE_DELAY_MAX_SECONDS,
        )
        return "delay", random.uniform(low, high)

    async def _save_dialog_context(
        self,
        user_id: UUID,
        incoming_texts: list[str],
        outgoing: list[OutgoingMessage],
    ) -> None:
        """Persist the turn into short-term history (ADR-10).

        Args:
            user_id: Internal user UUID.
            incoming_texts: Enriched user messages of the batch.
            outgoing: Replies published for the batch.
        """
        now = datetime.now(UTC)
        context: list[ContextMessage] = [
            ContextMessage(role="user", content=text, timestamp=now)
            for text in incoming_texts
        ]
        context += [
            ContextMessage(role="assistant", content=o.text, timestamp=now)
            for o in outgoing
        ]
        try:
            await self._redis.push_context_many(user_id, context)
        except Exception as exc:
            self._lg.error(f"Context not saved for {user_id}: {exc}")

    async def _handle_dtos(
        self,
        user: Users,
        dtos: list[IncomingMessage],
        enriched_texts: list[str] | None = None,
        delay_until: datetime | None = None,
    ) -> list[OutgoingMessage]:
        """Generate one reply for the whole batch (ADR-12 debounce).

        Assembles the interlocutor identity block, system prompt
        (ME/RULES/CREATOR), Redis history and the joined batch text,
        then routes through the dialogue chain.

        Args:
            user: Interlocutor owning the dialogue.
            dtos: Batch messages to answer.
            enriched_texts: Texts with media markers; None means
                enrich here (direct calls outside the batch cycle).
            delay_until: When set, delivery is postponed until this
                moment via the scheduled ZSet.

        Returns:
            The single reply wrapped in a list.
        """
        user_id = user.user_id
        limit = settings.LLM_HISTORY_LIMIT or _HISTORY_LIMIT_FALLBACK
        history = await self._redis.get_context(user_id, limit=limit)
        if enriched_texts is None:
            enriched_texts = await self._enrich_dtos(dtos)
        batch_text = "\n".join(t for t in enriched_texts if t)

        display_name = self._display_name(user)
        is_owner = user.role == "owner"
        identity_block = build_interlocutor_block(
            name=display_name,
            username=user.tg_username,
            external_id=dtos[0].user_external_id,
            channel=dtos[0].channel,
            is_owner=is_owner,
        )
        system_base = "\n\n".join(
            [load_system_prompt(is_owner=is_owner), identity_block]
        )
        memory_block = await self._long_term_block(user, batch_text)
        if memory_block:
            system_base = f"{system_base}\n\n{memory_block}"

        chat: list[ChatMessage] = [
            ChatMessage(role="system", content=system_base)
        ]
        for m in history:
            if m.role == "summary":
                chat.append(
                    ChatMessage(
                        role="system",
                        content=(
                            "Compressed memory of earlier conversations "
                            f"with {display_name}:\n" + m.content
                        ),
                    )
                )
            else:
                chat.append(ChatMessage(role=m.role, content=m.content))
        chat.append(ChatMessage(role="user", content=batch_text))

        response = await self._router.generate_with_fallback("dialogue", chat)
        reply_text = response.text or (
            "I lost my train of thought there. Could you repeat?"
        )
        self._lg.debug(f"LLM reply len={len(reply_text)}")

        outgoing = OutgoingMessage(
            user_id=user_id,
            channel=dtos[0].channel,
            text=reply_text,
            chat_external_id=dtos[0].chat_external_id,
            delay_until=delay_until,
        )
        await self._deliver(outgoing)
        await self._archive_batch(
            user_id,
            incoming=dtos,
            incoming_texts=enriched_texts,
            outgoing=[outgoing],
        )
        return [outgoing]

    async def _deliver(self, outgoing: OutgoingMessage) -> None:
        """Publish now or park the message in the scheduled ZSet.

        ADR-13: messages with a future ``delay_until`` never enter
        ``outgoing_messages`` early; the poller moves them there once
        the moment comes.

        Args:
            outgoing: Message produced for the user.
        """
        now = datetime.now(UTC)
        if outgoing.delay_until is not None and outgoing.delay_until > now:
            delay = (outgoing.delay_until - now).total_seconds()
            envelope = ScheduledEnvelope(kind="outgoing", outgoing=outgoing)
            await self._redis.push_scheduled(
                envelope.model_dump_json(), time.time() + delay
            )
            self._lg.info(
                f"Outgoing delayed by {delay:.0f}s: "
                f"user_id={outgoing.user_id}."
            )
            return
        await self._publisher.publish_outgoing(outgoing)

    async def _enrich_dtos(self, dtos: list[IncomingMessage]) -> list[str]:
        """Attach media descriptions to each DTO text once per batch.

        The marker ends up in both the dialogue prompt and the Redis
        history, so the next turn remembers what was on the image.

        Args:
            dtos: Batch messages as received from the channel.

        Returns:
            Texts aligned with ``dtos``, each with an optional media
            marker appended on a new line.
        """
        if self._media_recognizer is None:
            return [d.text for d in dtos]
        try:
            descriptions = await self._media_recognizer.describe_many(dtos)
        except Exception as exc:
            self._lg.error(f"Media enrichment failed: {exc}")
            return [d.text for d in dtos]
        enriched: list[str] = []
        for dto, description in zip(dtos, descriptions, strict=True):
            if not description:
                enriched.append(dto.text)
            elif dto.text:
                enriched.append(f"{dto.text}\n{description}")
            else:
                enriched.append(description)
        return enriched

    async def _long_term_block(self, user: Users, batch_text: str) -> str:
        """Assemble long-term memory lines for the system prompt.

        Combines the freshest PG facts (confidence-filtered) with
        semantically similar Qdrant hits, deduped by exact text.
        Degraded mode: any failure yields an empty block and ERROR
        log — dialogue continues without memory rather than crashing.

        Args:
            user: Interlocutor whose memory is assembled.
            batch_text: Joined text of the current batch (the query).

        Returns:
            Formatted memory block, or "" when nothing to add.
        """
        user_id = user.user_id
        try:
            async with self._session_factory() as session:
                facts = await HardFactsRepo(session).search_last_updated(
                    user_id, limit=settings.FACTS_IN_PROMPT_LIMIT
                )
            fact_lines = [
                f"- [{f.category}] {f.key}: {f.value.get('text', '')}"
                for f in facts
                if f.confidence >= settings.FACTS_MIN_CONFIDENCE
                and f.value.get("text")
            ]
        except Exception as exc:
            self._lg.error(f"Facts unavailable for {user_id}: {exc}")
            fact_lines = []

        hit_lines: list[str] = []
        if batch_text:
            try:
                query_vector = (await self._router.embed([batch_text]))[0]
                hits = await self._vectors.search(
                    user_id,
                    query_vector,
                    limit=settings.VECTOR_SEARCH_LIMIT,
                )
                known = set(fact_lines)
                hit_lines = [h for h in hits if h and h not in known]
                hit_lines = [f"- {line}" for line in hit_lines]
            except Exception as exc:
                self._lg.error(f"Vector search failed for {user_id}: {exc}")

        if not fact_lines and not hit_lines:
            return ""
        parts = [f"Long-term memory about {self._display_name(user)}:"]
        parts += fact_lines + hit_lines
        return "\n".join(parts)

    async def _archive_batch(
        self,
        user_id: UUID,
        incoming: list[IncomingMessage],
        incoming_texts: list[str],
        outgoing: list[OutgoingMessage],
    ) -> None:
        """Write the batch into the messages archive (ADR-14).

        Degraded mode: archive failure never breaks processing —
        log ERROR and continue (same policy as Qdrant, §9).

        Args:
            user_id: Internal user UUID.
            incoming: User messages of the batch.
            incoming_texts: Enriched texts aligned with ``incoming``.
            outgoing: Replies published for the batch.
        """
        rows = [
            Messages(
                user_id=user_id,
                direction="incoming",
                channel=dto.channel,
                text=text,
                source_kind=(
                    dto.media.kind
                    if dto.media is not None
                    else dto.media_type or "text"
                ),
            )
            for dto, text in zip(incoming, incoming_texts, strict=True)
        ]
        rows += [
            Messages(
                user_id=user_id,
                direction="outgoing",
                channel=message.channel,
                text=message.text,
            )
            for message in outgoing
        ]
        try:
            async with self._session_factory() as session:
                session.add_all(rows)
                await session.commit()
            self._lg.debug(f"Archived {len(rows)} messages.")
        except Exception as exc:
            self._lg.error(f"Messages archive failed for {user_id}: {exc}")

    async def _resolve_user(
        self,
        channel: str,
        external_id: str,
        *,
        sender_name: str | None = None,
        sender_username: str | None = None,
    ) -> Users:
        """Find a user by channel external id or auto-register one.

        Refreshes the stored profile (display name and Telegram
        username) on every call so the dialogue prompt always knows
        who the interlocutor is.

        Args:
            channel: Channel name ("telegram" only for now).
            external_id: Channel-specific user id.
            sender_name: Human-readable name from the channel.
            sender_username: Channel username without the leading "@".

        Returns:
            Existing or freshly created Users row.

        Raises:
            ValueError: Channel has no resolver yet.
            IntegrityError: Registration race lost and re-read failed.
        """
        if channel != "telegram":
            raise ValueError(f"Channel '{channel}' is not supported yet.")
        telegram_id = int(external_id)
        async with self._session_factory() as session:
            repo = UsersRepo(session)
            user = await repo.get_by_field("telegram_id", telegram_id)
            if user is not None:
                if self._apply_profile(
                    user, channel, sender_name, sender_username
                ):
                    await session.commit()
                return user
            from app.brain.repositories.security import hash_password

            user = Users(
                username=self._generated_username(telegram_id),
                telegram_id=telegram_id,
                display_name=self._clean_name(sender_name),
                tg_username=self._clean_username(sender_username),
                passwd_hash=hash_password(uuid4().hex),
                has_usable_password=False,
                role="user",
                settings={},
            )
            repo.add(user)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                user = await repo.get_by_field("telegram_id", telegram_id)
                if user is None:
                    raise
            self._lg.info(
                f"New user registered: telegram_id={telegram_id} -> "
                f"user_id={user.user_id}."
            )
            return user

    @staticmethod
    def _apply_profile(
        user: Users,
        channel: str,
        sender_name: str | None,
        sender_username: str | None,
    ) -> bool:
        """Refresh display name/username from the channel profile.

        Args:
            user: Persisted account to update in place.
            channel: Channel name.
            sender_name: Human-readable name from the channel.
            sender_username: Channel username without the leading "@".

        Returns:
            True when a field changed and the session must commit.
        """
        if sender_name is None:
            return False
        display_name = Worker._clean_name(sender_name)
        changed = user.display_name != display_name
        user.display_name = display_name
        if channel == "telegram":
            tg_username = Worker._clean_username(sender_username)
            if user.tg_username != tg_username:
                user.tg_username = tg_username
                changed = True
        return changed

    @staticmethod
    def _clean_name(sender_name: str | None) -> str | None:
        """Normalize a channel display name to the column limit."""
        if sender_name is None:
            return None
        return sender_name.strip()[:128] or None

    @staticmethod
    def _clean_username(sender_username: str | None) -> str | None:
        """Normalize a channel username to the column limit."""
        if sender_username is None:
            return None
        return sender_username.strip().lstrip("@")[:64] or None

    @staticmethod
    def _display_name(user: Users) -> str:
        """Human-readable name for prompts and memory headers."""
        return user.display_name or user.username

    @staticmethod
    def _generated_username(telegram_id: int) -> str:
        """Build a unique username for TG auto-registration.

        Args:
            telegram_id: Telegram user id.

        Returns:
            Username within the String(20) column limits.
        """
        return f"tg_{telegram_id}"[:20]

    async def _defer_incoming(
        self,
        dtos: list[IncomingMessage],
        amqp_messages: list[AbstractIncomingMessage],
        retry: int,
    ) -> None:
        """Busy-lock path: schedule retry or dead-letter (ADR-15).

        Args:
            dtos: Batch that could not be processed right now.
            amqp_messages: Raw deliveries to acknowledge (may be empty
                when called from the poller).
            retry: Current retry counter value.
        """
        envelope = ScheduledEnvelope(
            kind="incoming", incoming=dtos, retry_count=retry
        )
        if retry >= settings.MAX_SCHEDULED_RETRIES:
            self._lg.error(
                f"Batch exceeded MAX_SCHEDULED_RETRIES ({retry}) — DLQ."
            )
            await self._publisher.publish_dead_letter(
                envelope.model_dump_json().encode()
            )
        else:
            delay = settings.SCHEDULED_POLL_SECONDS
            await self._redis.push_scheduled(
                envelope.model_dump_json(),
                time.time() + delay,
            )
            self._lg.debug(f"Batch deferred by {delay}s (retry {retry}).")
        for amqp_message in amqp_messages:
            await amqp_message.ack()

    async def _scheduled_poller(self) -> None:
        """Move matured nodya:scheduled entries back into processing.

        Runs forever; per-entry failures are logged and swallowed so
        one bad envelope cannot kill the poller.
        """
        while True:
            await asyncio.sleep(settings.SCHEDULED_POLL_SECONDS)
            due = await self._redis.pop_due_scheduled(time.time())
            if due:
                self._lg.debug(f"Scheduled tasks matured: n={len(due)}")
            for raw in due:
                try:
                    envelope = ScheduledEnvelope.model_validate_json(raw)
                    await self._process_scheduled(envelope)
                except Exception as exc:
                    self._lg.error(f"Scheduled task failed: {exc}")

    async def _process_scheduled(self, envelope: ScheduledEnvelope) -> None:
        """Execute one matured scheduled task.

        Args:
            envelope: Parsed ZSet entry (incoming batch or outgoing).
        """
        if envelope.kind == "outgoing":
            assert envelope.outgoing is not None
            await self._deliver(envelope.outgoing)
            return
        assert envelope.incoming is not None
        dtos = envelope.incoming
        first = dtos[0]
        user = await self._resolve_user(
            first.channel,
            first.user_external_id,
            sender_name=first.sender_name,
            sender_username=first.sender_username,
        )
        lock_token = await self._redis.acquire_lock(
            user.user_id, ttl=_BATCH_LOCK_TTL_SECONDS
        )
        if lock_token is None:
            await self._defer_incoming(
                dtos, [], retry=envelope.retry_count + 1
            )
            return
        chat_id = _chat_id(first)
        await self._publish_typing(
            user.user_id, first.channel, "start", chat_id
        )
        try:
            await self._run_locked(user, dtos)
        finally:
            await self._publish_typing(
                user.user_id, first.channel, "stop", chat_id
            )
            await self._redis.release_lock(user.user_id, lock_token)
