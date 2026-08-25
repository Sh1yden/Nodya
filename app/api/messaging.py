"""Публикация сообщений в RabbitMQ.

Точка входа для Gateway; Worker и Sender'ы используют те же схемы,
но собственные consumer-подключения.
"""

import aio_pika
from aio_pika import DeliveryMode
from aio_pika.abc import (
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
)
from pydantic import BaseModel

from app.common import (
    DLX_EXCHANGE,
    ROUTING_INCOMING,
    ROUTING_OUTGOING,
    IncomingMessage,
    OutgoingMessage,
    declare_topology,
)
from app.core import LoggerMixin


class MessagePublisher(LoggerMixin):
    """Робастный издатель в exchange ``nodya`` (ADR-4)."""

    def __init__(self, rabbitmq_url: str) -> None:
        self._url = rabbitmq_url
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._exchange: AbstractRobustExchange | None = None
        self._dlx: AbstractRobustExchange | None = None

    @property
    def is_connected(self) -> bool:
        """True, если соединение с брокером установлено."""
        return bool(
            self._connection is not None and not self._connection.is_closed
        )

    async def connect(self) -> None:
        """Подключиться и задекларировать топологию."""
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        self._exchange = await declare_topology(self._channel)
        self._dlx = await self._channel.declare_exchange(
            DLX_EXCHANGE,
            aio_pika.ExchangeType.FANOUT,
            durable=True,
        )

    async def close(self) -> None:
        """Закрыть соединение при graceful shutdown."""
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()

    async def publish_incoming(self, message: IncomingMessage) -> None:
        """Опубликовать входящее сообщение (routing key ``incoming``)."""
        await self._publish(ROUTING_INCOMING, message)

    async def publish_outgoing(self, message: OutgoingMessage) -> None:
        """Опубликовать исходящее сообщение (routing key ``outgoing``)."""
        await self._publish(ROUTING_OUTGOING, message)

    async def publish_dead_letter(self, payload: bytes) -> None:
        """Отправить payload напрямую в DLQ (для задач из ZSet).

        Отложенные задачи уже ACK'нуты, поэтому nack недоступен —
        единственный путь в DLQ: прямая публикация в nodya.dlx.
        """
        if self._dlx is None:
            raise RuntimeError(
                "MessagePublisher не подключён: вызовите connect() первым."
            )
        broker_message = aio_pika.Message(
            body=payload,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await self._dlx.publish(broker_message, routing_key="")

    async def _publish(self, routing_key: str, body: BaseModel) -> None:
        if self._exchange is None:
            raise RuntimeError(
                "MessagePublisher не подключён: вызовите connect() первым."
            )
        payload = body.model_dump_json().encode()
        broker_message = aio_pika.Message(
            body=payload,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await self._exchange.publish(broker_message, routing_key=routing_key)
