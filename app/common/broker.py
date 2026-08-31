"""RabbitMQ topology: names, declarations, DLQ.

Every process declares the same topology (declarations are
idempotent), so Gateway/Worker/Sender startup order does not matter.
"""

from typing import Final

from aio_pika import ExchangeType
from aio_pika.abc import (
    AbstractRobustChannel,
    AbstractRobustExchange,
    AbstractRobustQueue,
)

EXCHANGE: Final = "nodya"
ROUTING_INCOMING: Final = "incoming"
ROUTING_OUTGOING: Final = "outgoing"
ROUTING_TYPING: Final = "typing"

QUEUE_INCOMING: Final = "incoming_messages"
QUEUE_OUTGOING: Final = "outgoing_messages"
QUEUE_TYPING: Final = "typing_events"

DLX_EXCHANGE: Final = "nodya.dlx"
QUEUE_DLQ: Final = "nodya_dlq"

_DEAD_LETTER_ARGUMENTS: Final[dict[str, str]] = {
    "x-dead-letter-exchange": DLX_EXCHANGE,
}


async def declare_topology(
    channel: AbstractRobustChannel,
) -> AbstractRobustExchange:
    """Declare exchanges, queues and bindings idempotently.

    Args:
        channel: Robust aio-pika channel to declare on.

    Returns:
        The main topic exchange used for publishing.
    """
    exchange = await channel.declare_exchange(
        EXCHANGE, ExchangeType.TOPIC, durable=True
    )
    dlx = await channel.declare_exchange(
        DLX_EXCHANGE, ExchangeType.FANOUT, durable=True
    )
    dlq = await channel.declare_queue(QUEUE_DLQ, durable=True)
    await dlq.bind(dlx, routing_key="")
    await declare_incoming_queue(channel, exchange)
    await declare_outgoing_queue(channel, exchange)
    await declare_typing_queue(channel, exchange)
    return exchange


async def declare_incoming_queue(
    channel: AbstractRobustChannel,
    exchange: AbstractRobustExchange,
) -> AbstractRobustQueue:
    """Declare the incoming queue routed to DLQ on rejection."""
    queue = await channel.declare_queue(
        QUEUE_INCOMING,
        durable=True,
        arguments=_DEAD_LETTER_ARGUMENTS,
    )
    await queue.bind(exchange, routing_key=ROUTING_INCOMING)
    return queue


async def declare_outgoing_queue(
    channel: AbstractRobustChannel,
    exchange: AbstractRobustExchange,
) -> AbstractRobustQueue:
    """Declare the outgoing queue routed to DLQ on rejection."""
    queue = await channel.declare_queue(
        QUEUE_OUTGOING,
        durable=True,
        arguments=_DEAD_LETTER_ARGUMENTS,
    )
    await queue.bind(exchange, routing_key=ROUTING_OUTGOING)
    return queue


async def declare_typing_queue(
    channel: AbstractRobustChannel,
    exchange: AbstractRobustExchange,
) -> AbstractRobustQueue:
    """Declare the typing events queue (no DLQ needed, ephemeral)."""
    queue = await channel.declare_queue(
        QUEUE_TYPING,
        durable=True,
        arguments=_DEAD_LETTER_ARGUMENTS,
    )
    await queue.bind(exchange, routing_key=ROUTING_TYPING)
    return queue
