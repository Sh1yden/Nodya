"""Unit tests for the RabbitMQ queue collector."""

from types import SimpleNamespace

import pytest

from app.admin import collectors
from app.admin.collectors.broker import BrokerCollector, QueueInfo
from app.common.broker import QUEUE_INCOMING


class _Declaration:
    message_count = 5
    consumer_count = 2


class _FakeChannel:
    def __init__(self, fail: bool) -> None:
        self._fail = fail
        self.is_closed = False
        self.closed = False

    async def declare_queue(self, name: str, passive: bool = False) -> object:
        assert passive is True
        if self._fail:
            raise RuntimeError("NOT_FOUND")
        return SimpleNamespace(declaration_result=_Declaration())

    async def close(self) -> None:
        self.closed = True
        self.is_closed = True


class _FakeConnection:
    def __init__(self, failing: set[str]) -> None:
        self._failing = failing
        self.is_closed = False
        self.channels: list[_FakeChannel] = []

    async def channel(self) -> _FakeChannel:
        channel = _FakeChannel(fail=len(self.channels) in self._failing)
        self.channels.append(channel)
        return channel


async def test_get_queues_reads_counters_per_channel() -> None:
    connection = _FakeConnection(failing=set())
    collector = BrokerCollector("amqp://guest:guest@broker/")
    collector._connection = connection
    queues = await collector.get_queues()
    assert len(queues) == 4
    assert queues[0] == QueueInfo(QUEUE_INCOMING, 5, 2)
    assert all(channel.closed for channel in connection.channels)


async def test_missing_queue_fails_only_its_row() -> None:
    connection = _FakeConnection(failing={0})
    collector = BrokerCollector("amqp://guest:guest@broker/")
    collector._connection = connection
    queues = await collector.get_queues()
    assert queues[0].error == "NOT_FOUND"
    assert queues[1].error is None
    assert queues[1].messages == 5
    assert connection.channels[0].closed


async def test_ping_false_when_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(url: str, **kwargs: object) -> None:
        raise OSError("no broker")

    monkeypatch.setattr(collectors.broker.aio_pika, "connect", boom)
    collector = BrokerCollector("amqp://guest:guest@broker/")
    assert not await collector.ping()
    assert collector._connection is None


async def test_close_without_connection_is_noop() -> None:
    collector = BrokerCollector("amqp://guest:guest@broker/")
    await collector.close()
