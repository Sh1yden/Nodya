"""RabbitMQ queue depths via passive declarations."""

import asyncio
from dataclasses import dataclass
from typing import Any

import aio_pika

from app.common.broker import (
    QUEUE_DLQ,
    QUEUE_INCOMING,
    QUEUE_OUTGOING,
    QUEUE_TYPING,
)

QUEUE_NAMES = (QUEUE_INCOMING, QUEUE_OUTGOING, QUEUE_TYPING, QUEUE_DLQ)
_CONNECT_TIMEOUT_SECONDS = 5.0
_CHANNEL_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class QueueInfo:
    """Depth and consumer count of one queue."""

    name: str
    messages: int | None = None
    consumers: int | None = None
    error: str | None = None


class BrokerCollector:
    """Read queue depths without ever declaring new topology."""

    def __init__(
        self,
        url: str,
        connect_timeout: float = _CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        """Configure the collector.

        Args:
            url: AMQP connection URL.
            connect_timeout: Limit for opening a connection, seconds.
        """
        self._url = url
        self._connect_timeout = connect_timeout
        self._connection: aio_pika.abc.AbstractConnection | None = None

    async def _connection_or_connect(
        self,
    ) -> aio_pika.abc.AbstractConnection:
        """Return a live connection, opening one when needed.

        Returns:
            An open AMQP connection.
        """
        if self._connection is None or self._connection.is_closed:
            self._connection = await asyncio.wait_for(
                aio_pika.connect(self._url),
                timeout=self._connect_timeout,
            )
        return self._connection

    async def ping(self) -> bool:
        """Check broker reachability through a live connection.

        Returns:
            True when a connection can be established.
        """
        try:
            await self._connection_or_connect()
            return True
        except Exception:
            self._connection = None
            return False

    async def get_queues(self) -> list[QueueInfo]:
        """Passively declare every known queue and read its counters.

        A missing queue only fails its own row: each queue gets a
        fresh short-lived channel, so a closed channel never affects
        the following queues.

        Returns:
            One QueueInfo per known queue, in declaration order.
        """
        connection = await self._connection_or_connect()
        results = [
            await self._read_queue(connection, name) for name in QUEUE_NAMES
        ]
        return results

    async def _read_queue(
        self,
        connection: aio_pika.abc.AbstractConnection,
        name: str,
    ) -> QueueInfo:
        """Read counters of a single queue through a passive declare.

        Args:
            connection: Open AMQP connection.
            name: Queue name.

        Returns:
            Counters or a per-queue error.
        """
        channel: aio_pika.abc.AbstractChannel | None = None
        try:
            channel = await asyncio.wait_for(
                connection.channel(), timeout=_CHANNEL_TIMEOUT_SECONDS
            )
            queue = await asyncio.wait_for(
                channel.declare_queue(name, passive=True),
                timeout=_CHANNEL_TIMEOUT_SECONDS,
            )
            result: Any = queue.declaration_result
            return QueueInfo(
                name=name,
                messages=int(result.message_count),
                consumers=int(result.consumer_count),
            )
        except Exception as exc:
            return QueueInfo(
                name=name,
                error=str(exc) or type(exc).__name__,
            )
        finally:
            if channel is not None and not channel.is_closed:
                await channel.close()

    async def close(self) -> None:
        """Close the shared AMQP connection when open."""
        connection = self._connection
        self._connection = None
        if connection is not None and not connection.is_closed:
            await connection.close()
