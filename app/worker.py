"""Worker: consumer очереди incoming_messages.

Полный цикл батча (ADR-12/15):
1. Debounce: фиксированное окно DEBOUNCE_SECONDS с первого сообщения;
   пачка копится в памяти, AMQP-сообщения не ACK до обработки.
2. resolve_user: поиск/авторегистрация по external_id канала.
3. Лок пользователя; занят -> пачка в nodya:scheduled (+30s),
   retry_count >= MAX_SCHEDULED_RETRIES -> DLQ.
4. Обработка (срез C: эхо; LLM появится в Этапе 5).
5. Публикация OutgoingMessage и ACK всей пачки.

Отдельный poller каждые SCHEDULED_POLL_SECONDS переносит созревшие
элементы nodya:scheduled обратно в обработку (ADR-13).
"""

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import aio_pika
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustQueue,
)
from sqlalchemy.exc import IntegrityError

from app.api.messaging import MessagePublisher
from app.brain.memory.short.redis import RedisClient
from app.brain.models import Messages, Users
from app.brain.repositories import UsersRepo
from app.common.broker import (
    declare_incoming_queue,
    declare_topology,
)
from app.common.schemas import (
    IncomingMessage,
    OutgoingMessage,
    ScheduledEnvelope,
)
from app.core import LoggerMixin, settings

_PREFETCH_COUNT = 50


@dataclass(slots=True)
class _PendingBatch:
    """Пачка в debounce-буфере: AMQP-конверты + DTO."""

    amqp_messages: list[AbstractIncomingMessage] = field(default_factory=list)
    dtos: list[IncomingMessage] = field(default_factory=list)


class Worker(LoggerMixin):
    """Обработчик входящих сообщений (Этапы 4–5, срез C: эхо)."""

    def __init__(
        self,
        broker_url: str,
        redis_client: RedisClient,
        session_factory: type,
        publisher: MessagePublisher,
    ) -> None:
        self._url = broker_url
        self._redis = redis_client
        self._session_factory = session_factory
        self._publisher = publisher
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._queue: AbstractRobustQueue | None = None
        self._running = False
        self._poller_task: asyncio.Task | None = None
        self._buffers: dict[str, _PendingBatch] = {}
        self._timers: dict[str, asyncio.Task] = {}

    async def run(self) -> None:
        """Подключиться к брокеру и начать consume."""
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
        """Graceful shutdown: необработанные сообщения вернутся в очередь."""
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

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        """Приём из очереди: положить в debounce-буфер юзера."""
        try:
            dto = IncomingMessage.model_validate_json(message.body)
        except ValueError:
            self._lg.warning("Битый payload во входящей очереди.")
            await message.nack(requeue=False)
            return

        key = dto.user_external_id
        self._lg.info(
            "Сообщение получено: user=%s channel=%s len=%d.",
            key,
            dto.channel,
            len(dto.text),
        )
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

    async def _flush_after_debounce(self, key: str) -> None:
        """Дождаться окна тишины и обработать накопленную пачку."""
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(settings.DEBOUNCE_SECONDS)
        batch = self._buffers.pop(key, None)
        self._timers.pop(key, None)
        if batch is None or not self._running:
            return
        self._lg.info(
            "Debounce завершён: пачка n=%d user=%s.",
            len(batch.dtos),
            key,
        )
        try:
            await self._process_batch(batch.dtos, batch.amqp_messages)
        except Exception:
            self._lg.exception(
                "Пачка user_external_id=%s упала, уходит в DLQ.", key
            )
            for amqp_message in batch.amqp_messages:
                await amqp_message.nack(requeue=False)

    async def _process_batch(
        self,
        dtos: list[IncomingMessage],
        amqp_messages: list[AbstractIncomingMessage],
    ) -> None:
        """Лок -> обработка -> публикация -> ACK пачки."""
        first = dtos[0]
        user = await self._resolve_user(first.channel, first.user_external_id)
        lock_token = await self._redis.acquire_lock(user.user_id)
        if lock_token is None:
            self._lg.warning(
                "Лок user_id=%s занят, пачка отложена на %ss.",
                user.user_id,
                settings.SCHEDULED_POLL_SECONDS,
            )
            await self._defer_incoming(dtos, amqp_messages, retry=0)
            return
        try:
            await self._handle_dtos(user.user_id, dtos)
        finally:
            await self._redis.release_lock(user.user_id, lock_token)
        self._lg.info(
            "Пачка обработана и опубликована: n=%d user_id=%s.",
            len(dtos),
            user.user_id,
        )
        for amqp_message in amqp_messages:
            await amqp_message.ack()

    async def _handle_dtos(
        self, user_id: UUID, dtos: list[IncomingMessage]
    ) -> None:
        """Срез C: эхо вместо LLM (LLM подключается в Этапе 5)."""
        outgoing: list[OutgoingMessage] = []
        for dto in dtos:
            message = OutgoingMessage(
                user_id=user_id,
                channel=dto.channel,
                text=dto.text,
            )
            await self._publisher.publish_outgoing(message)
            outgoing.append(message)
        await self._archive_batch(user_id, incoming=dtos, outgoing=outgoing)

    async def _archive_batch(
        self,
        user_id: UUID,
        incoming: list[IncomingMessage],
        outgoing: list[OutgoingMessage],
    ) -> None:
        """Записать пачку в messages (ADR-14).

        Деградация: сбой архива не роняет обработку — лог ERROR
        и продолжение (аналогично Qdrant в §9).
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
        except Exception:
            self._lg.exception(
                "Архив messages не записан для user_id=%s.", user_id
            )

    async def _resolve_user(self, channel: str, external_id: str) -> Users:
        """Найти пользователя по external_id канала или создать."""
        if channel != "telegram":
            raise ValueError(f"Канал {channel} пока не поддерживается.")
        telegram_id = int(external_id)
        async with self._session_factory() as session:
            repo = UsersRepo(session)
            user = await repo.get_by_field("telegram_id", telegram_id)
            if user is not None:
                return user
            user = Users(
                username=self._generated_username(telegram_id),
                telegram_id=telegram_id,
                passwd_hash=uuid4().hex,
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
                "Новый пользователь: telegram_id=%s -> user_id=%s.",
                telegram_id,
                user.user_id,
            )
            return user

    @staticmethod
    def _generated_username(telegram_id: int) -> str:
        """Username для TG-авторегистрации (уникален, до 20 символов)."""
        return f"tg_{telegram_id}"[:20]

    async def _defer_incoming(
        self,
        dtos: list[IncomingMessage],
        amqp_messages: list[AbstractIncomingMessage],
        retry: int,
    ) -> None:
        """Занятый лок: nodya:scheduled(+30s) либо DLQ (ADR-15)."""
        envelope = ScheduledEnvelope(
            kind="incoming", incoming=dtos, retry_count=retry
        )
        if retry >= settings.MAX_SCHEDULED_RETRIES:
            self._lg.error(
                "Пачка превышает MAX_SCHEDULED_RETRIES (%s) — DLQ.",
                retry,
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
        for amqp_message in amqp_messages:
            await amqp_message.ack()

    async def _scheduled_poller(self) -> None:
        """Перенос созревших элементов nodya:scheduled в работу."""
        while True:
            await asyncio.sleep(settings.SCHEDULED_POLL_SECONDS)
            due = await self._redis.pop_due_scheduled(time.time())
            for raw in due:
                try:
                    envelope = ScheduledEnvelope.model_validate_json(raw)
                    await self._process_scheduled(envelope)
                except Exception:
                    self._lg.exception(
                        "Ошибка обработки отложенной задачи: %.200s", raw
                    )

    async def _process_scheduled(self, envelope: ScheduledEnvelope) -> None:
        """Выполнить одну созревшую задачу из ZSet."""
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
        try:
            await self._handle_dtos(user.user_id, dtos)
        finally:
            await self._redis.release_lock(user.user_id, lock_token)
