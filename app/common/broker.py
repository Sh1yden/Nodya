"""Топология RabbitMQ: имена, декларации, DLQ.

Все процессы объявляют одну и ту же топологию (декларации идемпотентны),
поэтому порядок старта Gateway/Worker/Sender не важен.
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

QUEUE_INCOMING: Final = "incoming_messages"
QUEUE_OUTGOING: Final = "outgoing_messages"

DLX_EXCHANGE: Final = "nodya.dlx"
QUEUE_DLQ: Final = "nodya_dlq"

_DEAD_LETTER_ARGUMENTS: Final[dict[str, str]] = {
    "x-dead-letter-exchange": DLX_EXCHANGE,
}


async def declare_topology(
    channel: AbstractRobustChannel,
) -> AbstractRobustExchange:
    """Задекларировать exchange'и, очереди и биндинги.

    Возвращает основной exchange для публикации.
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
    return exchange


async def declare_incoming_queue(
    channel: AbstractRobustChannel,
    exchange: AbstractRobustExchange,
) -> AbstractRobustQueue:
    """Очередь входящих с маршрутизацией отказов в DLQ."""
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
    """Очередь исходящих с маршрутизацией отказов в DLQ."""
    queue = await channel.declare_queue(
        QUEUE_OUTGOING,
        durable=True,
        arguments=_DEAD_LETTER_ARGUMENTS,
    )
    await queue.bind(exchange, routing_key=ROUTING_OUTGOING)
    return queue
