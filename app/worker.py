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
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
from app.brain import load_system_prompt
from app.brain.llm_choice import ChatMessage, LLMRouter
from app.brain.memory.short import ContextMessage, RedisClient
from app.brain.memory.vector import VectorMemory
from app.brain.models import HardFacts, Messages, Users
from app.brain.repositories import HardFactsRepo, UsersRepo
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
_LINK_COMMAND_PATTERN = re.compile(
    r"^/(?:link|start)\s+([A-Za-z0-9]{6,12})$", re.IGNORECASE
)
_HISTORY_LIMIT_FALLBACK = 20


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
    ) -> None:
        """Store collaborators; no I/O happens here.

        Args:
            broker_url: AMQP DSN for the incoming consumer.
            redis_client: Short-term memory client (locks/scheduled).
            session_factory: Async session factory for DB access.
            publisher: Outgoing publisher into RabbitMQ.
            router: LLM router used for dialogue generation.
            vectors: Qdrant-backed long-term fact memory.
        """
        self._url = broker_url
        self._redis = redis_client
        self._session_factory = session_factory
        self._publisher = publisher
        self._router = router
        self._vectors = vectors
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._queue: AbstractRobustQueue | None = None
        self._running = False
        self._poller_task: asyncio.Task | None = None
        self._buffers: dict[str, _PendingBatch] = {}
        self._timers: dict[str, asyncio.Task] = {}

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
        """Intercept pairing commands before the dialogue machinery.

        Commands bypass debounce/state/LLM/archive completely: they
        are acknowledged immediately after handling so the memory of
        the conversation stays clean.

        Args:
            dto: Parsed incoming message.
            message: Raw AMQP delivery to acknowledge.

        Returns:
            True when the message was a handled command.
        """
        match = _LINK_COMMAND_PATTERN.match(dto.text.strip())
        if not match:
            return False
        try:
            await self._handle_link(dto, message, match.group(1))
        except Exception as exc:
            self._lg.error(f"/link handling failed: {exc}")
            await self._ack_with_reply(
                dto, message, "Не смог связать аккаунт — попробуй позже."
            )
        return True

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
        local_user = await self._resolve_user("telegram", dto.user_external_id)
        await self._publisher.publish_outgoing(
            OutgoingMessage(
                user_id=local_user.user_id,
                channel=dto.channel,
                text=text,
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
        user = await self._resolve_user(first.channel, first.user_external_id)
        lock_token = await self._redis.acquire_lock(user.user_id)
        if lock_token is None:
            self._lg.warning(
                f"Lock busy for user_id={user.user_id}, postponing "
                f"batch by {settings.SCHEDULED_POLL_SECONDS}s."
            )
            await self._defer_incoming(dtos, amqp_messages, retry=0)
            return
        # Typing indicator (ADR-17): publish start/stop via RabbitMQ,
        # consumed by channel senders; failures must not break batch.
        await self._publish_typing(user.user_id, first.channel, "start")
        try:
            await self._run_locked(user.user_id, dtos)
        finally:
            await self._publish_typing(user.user_id, first.channel, "stop")
            await self._redis.release_lock(user.user_id, lock_token)
        self._lg.debug(
            f"Batch processed: n={len(dtos)} user_id={user.user_id}"
        )
        for amqp_message in amqp_messages:
            await amqp_message.ack()

    async def _publish_typing(
        self, user_id: UUID, channel: str, action: str
    ) -> None:
        """Publish typing event, suppressing errors.

        Args:
            user_id: Internal user UUID.
            channel: Channel name.
            action: ``start`` or ``stop``.
        """
        try:
            await self._publisher.publish_typing(
                TypingEvent(user_id=user_id, channel=channel, action=action)
            )
        except Exception as exc:
            self._lg.warning(f"Typing {action} failed for {user_id}: {exc}")

    async def _run_locked(
        self, user_id: UUID, dtos: list[IncomingMessage]
    ) -> list[OutgoingMessage]:
        """Handle a batch under an already-taken lock.

        Sets thinking with a TTL (a crashed worker releases the state
        automatically), asks the proactive decision stub, generates
        the reply, saves context and returns to idle.

        Args:
            user_id: Internal user UUID.
            dtos: Batch messages to answer.

        Returns:
            Published outgoing messages.
        """
        decision = self._proactive_decision()
        if decision != "now":
            # TODO(stage-7): implement delay/skip branches together
            # with the RSS parser and delayed outgoing.
            self._lg.debug(f"Proactive decision={decision} for {user_id}")
        await self._redis.set_state(
            user_id, "thinking", ttl=_THINKING_TTL_SECONDS
        )
        self._lg.debug(f"State thinking: user_id={user_id}")
        outgoing = await self._handle_dtos(user_id, dtos)
        await self._save_dialog_context(user_id, dtos, outgoing)
        await self._redis.set_state(user_id, "idle")
        self._lg.debug(f"State idle: user_id={user_id}")
        return outgoing

    @staticmethod
    def _proactive_decision() -> str:
        """Decide how to react to a batch.

        Stub of TODO_MASTER stage 4: always answers immediately.
        The 70% now / 20% delay / 10% skip random arrives together
        with proactivity work (stage 7).

        Returns:
            One of "now" / "delay" / "skip"; currently always "now".
        """
        return "now"

    async def _save_dialog_context(
        self,
        user_id: UUID,
        incoming: list[IncomingMessage],
        outgoing: list[OutgoingMessage],
    ) -> None:
        """Persist the turn into short-term history (ADR-10).

        Args:
            user_id: Internal user UUID.
            incoming: User messages of the batch.
            outgoing: Replies published for the batch.
        """
        now = datetime.now(UTC)
        context: list[ContextMessage] = [
            ContextMessage(role="user", content=d.text, timestamp=now)
            for d in incoming
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
        self, user_id: UUID, dtos: list[IncomingMessage]
    ) -> list[OutgoingMessage]:
        """Generate one reply for the whole batch (ADR-12 debounce).

        Assembles system prompt (ME/RULES), Redis history and the
        joined batch text, then routes through the dialogue chain.

        Args:
            user_id: Internal user UUID.
            dtos: Batch messages to answer.

        Returns:
            The single published reply wrapped in a list.
        """
        limit = settings.LLM_HISTORY_LIMIT or _HISTORY_LIMIT_FALLBACK
        history = await self._redis.get_context(user_id, limit=limit)
        batch_text = "\n".join(d.text for d in dtos)

        system_base = load_system_prompt()
        memory_block = await self._long_term_block(user_id, batch_text)
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
                            "Compressed memory of earlier conversations:\n"
                            + m.content
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
        )
        await self._publisher.publish_outgoing(outgoing)
        await self._archive_batch(user_id, incoming=dtos, outgoing=[outgoing])
        return [outgoing]

    async def _long_term_block(self, user_id: UUID, batch_text: str) -> str:
        """Assemble long-term memory lines for the system prompt.

        Combines the freshest PG facts (confidence-filtered) with
        semantically similar Qdrant hits, deduped by exact text.
        Degraded mode: any failure yields an empty block and ERROR
        log — dialogue continues without memory rather than crashing.

        Args:
            user_id: Internal user UUID.
            batch_text: Joined text of the current batch (the query).

        Returns:
            Formatted memory block, or "" when nothing to add.
        """
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
        parts = ["Long-term memory about this user:"]
        parts += fact_lines + hit_lines
        return "\n".join(parts)

    async def _archive_batch(
        self,
        user_id: UUID,
        incoming: list[IncomingMessage],
        outgoing: list[OutgoingMessage],
    ) -> None:
        """Write the batch into the messages archive (ADR-14).

        Degraded mode: archive failure never breaks processing —
        log ERROR and continue (same policy as Qdrant, §9).

        Args:
            user_id: Internal user UUID.
            incoming: User messages of the batch.
            outgoing: Replies published for the batch.
        """
        rows = [
            Messages(
                user_id=user_id,
                direction="incoming",
                channel=dto.channel,
                text=dto.text,
            )
            for dto in incoming
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

    async def _resolve_user(self, channel: str, external_id: str) -> Users:
        """Find a user by channel external id or auto-register one.

        Args:
            channel: Channel name ("telegram" only for now).
            external_id: Channel-specific user id.

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
                return user
            from app.brain.repositories.security import hash_password

            user = Users(
                username=self._generated_username(telegram_id),
                telegram_id=telegram_id,
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
            await self._publisher.publish_outgoing(envelope.outgoing)
            return
        assert envelope.incoming is not None
        dtos = envelope.incoming
        first = dtos[0]
        user = await self._resolve_user(first.channel, first.user_external_id)
        lock_token = await self._redis.acquire_lock(user.user_id)
        if lock_token is None:
            await self._defer_incoming(
                dtos, [], retry=envelope.retry_count + 1
            )
            return
        await self._publish_typing(user.user_id, first.channel, "start")
        try:
            await self._run_locked(user.user_id, dtos)
        finally:
            await self._publish_typing(user.user_id, first.channel, "stop")
            await self._redis.release_lock(user.user_id, lock_token)
