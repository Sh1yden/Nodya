"""Unit tests for the Docker collector and log demultiplexing."""

from datetime import UTC, datetime

import httpx

from app.admin.collectors.docker import (
    DockerCollector,
    _format_ports,
    _parse_container,
    _parse_rfc3339,
    _split_timestamp,
    demux_logs,
)


def _frame(stream: int, text: str) -> bytes:
    payload = text.encode()
    header = bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big")
    return header + payload


def test_parse_rfc3339_nanoseconds() -> None:
    parsed = _parse_rfc3339("2026-10-29T12:00:00.123456789Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.year == 2026
    assert parsed.microsecond == 123456


def test_parse_rfc3339_zero_and_garbage() -> None:
    assert _parse_rfc3339("0001-01-01T00:00:00Z") is None
    assert _parse_rfc3339("not-a-date") is None
    assert _parse_rfc3339("") is None


def test_split_timestamp() -> None:
    stamp, message = _split_timestamp("2026-10-29T12:00:00.5Z hello world")
    assert stamp is not None
    assert message == "hello world"
    stamp, message = _split_timestamp("plain message")
    assert stamp is None
    assert message == "plain message"


def test_demux_multiplexed_frames() -> None:
    payload = _frame(1, "hello\n") + _frame(2, "oops\n")
    assert demux_logs(payload) == [
        ("stdout", "hello"),
        ("stderr", "oops"),
    ]


def test_demux_raw_tty_output() -> None:
    payload = b"2026-10-29T12:00:00Z first\nsecond\n"
    assert demux_logs(payload) == [
        ("stdout", "2026-10-29T12:00:00Z first"),
        ("stdout", "second"),
    ]


def test_demux_ignores_trailing_partial_frame() -> None:
    payload = _frame(1, "complete\n") + b"\x01\x00\x00\x00\x00\x00\x00"
    assert demux_logs(payload) == [("stdout", "complete")]


def test_demux_empty_payload() -> None:
    assert demux_logs(b"") == []


def test_format_ports() -> None:
    ports = [
        {"PrivatePort": 8014, "PublicPort": 8014, "Type": "tcp"},
        {"PrivatePort": 5432, "Type": "tcp"},
        {"Type": "udp"},
    ]
    assert _format_ports(ports) == ["8014->8014/tcp", "5432/tcp"]


def test_parse_container_uses_compose_service() -> None:
    summary = {
        "Id": "abcdef1234567890",
        "Names": ["/nodya_heart"],
        "Image": "nodya-app:local",
        "State": "running",
        "Status": "Up 2 hours",
        "Labels": {"com.docker.compose.service": "app"},
        "Ports": [{"PrivatePort": 8014, "Type": "tcp"}],
    }
    info = _parse_container(summary)
    assert info.service == "app"
    assert info.name == "nodya_heart"
    assert info.short_id == "abcdef123456"
    assert info.ports == ("8014/tcp",)


def _transport(
    containers: list[dict],
    inspect: dict[str, dict],
    logs: bytes = b"",
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/containers/json":
            assert "filters" in request.url.params
            return httpx.Response(200, json=containers)
        if path.endswith("/logs"):
            assert request.url.params["tail"] == "2"
            return httpx.Response(200, content=logs)
        name = path.split("/")[2]
        if name in inspect:
            return httpx.Response(200, json=inspect[name])
        return httpx.Response(500, json={"message": "broken"})

    return httpx.MockTransport(handler)


async def test_get_containers_merges_inspect_fields() -> None:
    containers = [
        {
            "Id": "aaa",
            "Names": ["/nodya_heart"],
            "Image": "nodya-app:local",
            "State": "running",
            "Status": "Up",
            "Labels": {"com.docker.compose.service": "app"},
        },
        {
            "Id": "bbb",
            "Names": ["/nodya_broker"],
            "Image": "rabbitmq:4",
            "State": "running",
            "Status": "Up",
            "Labels": {"com.docker.compose.service": "rabbitmq"},
        },
    ]
    inspect = {
        "aaa": {
            "State": {
                "Status": "running",
                "Health": {"Status": "healthy"},
                "StartedAt": "2026-10-29T10:00:00.5Z",
            },
            "RestartCount": 2,
        }
    }
    client = httpx.AsyncClient(transport=_transport(containers, inspect))
    collector = DockerCollector(client, "http://docker-proxy:2375", "nodya")
    infos = await collector.get_containers()
    await client.aclose()
    assert [item.service for item in infos] == ["app", "rabbitmq"]
    app_info = infos[0]
    assert app_info.health == "healthy"
    assert app_info.restart_count == 2
    assert app_info.started_at == datetime(
        2026, 10, 29, 10, 0, 0, 500000, tzinfo=UTC
    )
    assert infos[1].health is None


async def test_get_logs_parses_lines_and_timestamps() -> None:
    payload = _frame(1, "2026-10-29T12:00:00.25Z line one\n")
    containers = [
        {
            "Id": "aaa",
            "Names": ["/nodya_heart"],
            "State": "running",
            "Labels": {"com.docker.compose.service": "app"},
        }
    ]
    client = httpx.AsyncClient(
        transport=_transport(containers, {}, logs=payload)
    )
    collector = DockerCollector(client, "http://docker-proxy:2375", "nodya")
    lines = await collector.get_logs("aaa", tail=2)
    await client.aclose()
    assert len(lines) == 1
    assert lines[0].stream == "stdout"
    assert lines[0].message == "line one"
    assert lines[0].timestamp is not None


async def test_ping_reports_proxy_state() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="OK")

    client = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    collector = DockerCollector(client, "http://docker-proxy:2375", "nodya")
    assert await collector.ping()
    await client.aclose()


async def test_ping_false_on_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    collector = DockerCollector(client, "http://docker-proxy:2375", "nodya")
    assert not await collector.ping()
    await client.aclose()
