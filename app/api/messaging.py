"""Publishing messages to RabbitMQ.

Entry point for the Gateway; Worker and Senders use the same schemas
but own their consumer connections.
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
    """Robust publisher into the ``nodya`` exchange (ADR-4)."""

    def __init__(self, rabbitmq_url: str) -> None:
        """Store connection parameters (no I/O here).

        Args:
            rabbitmq_url: AMQP DSN, see SettingsSchema.rabbitmq_url.
        """
        self._url = rabbitmq_url
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None
        self._exchange: AbstractRobustExchange | None = None
        self._dlx: AbstractRobustExchange | None = None

    @property
    def is_connected(self) -> bool:
        """Whether the broker connection is currently open."""
        return bool(
            self._connection is not None and not self._connection.is_closed
        )

    async def connect(self) -> None:
        """Connect to the broker and declare the topology."""
        self._lg.debug("Connecting to RabbitMQ...")
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        self._exchange = await declare_topology(self._channel)
        self._dlx = await self._channel.declare_exchange(
            DLX_EXCHANGE,
            aio_pika.ExchangeType.FANOUT,
            durable=True,
        )
        self._lg.debug("RabbitMQ connected and topology declared.")

    async def close(self) -> None:
        """Close the connection on graceful shutdown."""
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
            self._lg.debug("RabbitMQ publisher closed.")

    async def publish_incoming(self, message: IncomingMessage) -> None:
        """Publish an incoming message (routing key ``incoming``)."""
        await self._publish(ROUTING_INCOMING, message)

    async def publish_outgoing(self, message: OutgoingMessage) -> None:
        """Publish an outgoing message (routing key ``outgoing``)."""
        await self._publish(ROUTING_OUTGOING, message)

    async def publish_dead_letter(self, payload: bytes) -> None:
        """Send a payload straight to the DLQ (for ZSet tasks).

        Postponed tasks are already ACKed, so nack is unavailable —
        publishing into nodya.dlx directly is their only DLQ path.
        """
        if self._dlx is None:
            raise RuntimeError(
                "MessagePublisher is not connected; call connect() first."
            )
        broker_message = aio_pika.Message(
            body=payload,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await self._dlx.publish(broker_message, routing_key="")
        self._lg.error("Payload published to DLQ directly.")

    async def _publish(self, routing_key: str, body: BaseModel) -> None:
        """Serialize a DTO and publish it persistently.

        Args:
            routing_key: Target routing key inside the main exchange.
            body: Pydantic model to serialize.

        Raises:
            RuntimeError: Publisher was not connected.
        """
        if self._exchange is None:
            raise RuntimeError(
                "MessagePublisher is not connected; call connect() first."
            )
        payload = body.model_dump_json().encode()
        broker_message = aio_pika.Message(
            body=payload,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await self._exchange.publish(broker_message, routing_key=routing_key)
