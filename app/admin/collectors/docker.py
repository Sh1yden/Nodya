"""Container state and logs via the read-only Docker socket proxy."""

import asyncio
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

_MAX_TAIL = 500
_RFC3339_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,9})?"
    r"(Z|[+-]\d{2}:\d{2})$"
)
_FRAME_HEADER_SIZE = 8


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    """State of one compose container."""

    id: str
    name: str
    service: str
    image: str
    state: str
    status: str
    health: str | None = None
    started_at: datetime | None = None
    restart_count: int | None = None
    ports: tuple[str, ...] = ()

    @property
    def short_id(self) -> str:
        """First 12 characters of the container id."""
        return self.id[:12]


@dataclass(frozen=True, slots=True)
class ContainerLogLine:
    """One line of a container log stream."""

    timestamp: datetime | None
    stream: str
    message: str


def _parse_rfc3339(value: str) -> datetime | None:
    """Parse Docker's RFC3339Nano timestamp into an aware datetime.

    Args:
        value: Timestamp string with up to nine fractional digits.

    Returns:
        Parsed datetime, or None when malformed or a zero value.
    """
    match = _RFC3339_RE.match(value.strip())
    if match is None:
        return None
    base, fraction, zone = match.groups()
    text = base
    if fraction:
        text += fraction[:7]
    text += "+00:00" if zone == "Z" else zone
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return None if parsed.year < 2000 else parsed


def _split_timestamp(line: str) -> tuple[datetime | None, str]:
    """Split a leading Docker timestamp off a log line.

    Args:
        line: Raw log line, optionally starting with an RFC3339 stamp.

    Returns:
        Parsed timestamp (or None) and the remaining message.
    """
    head, separator, rest = line.partition(" ")
    if separator:
        parsed = _parse_rfc3339(head)
        if parsed is not None:
            return parsed, rest
    return None, line


def _decode_chunk(stream: str, chunk: bytes) -> list[tuple[str, str]]:
    """Decode a frame payload into (stream, line) pairs.

    Args:
        stream: Label for the originating stream (stdout/stderr).
        chunk: Raw bytes of one multiplexed frame.

    Returns:
        Non-empty lines with the given stream label.
    """
    text = chunk.decode("utf-8", errors="replace")
    return [(stream, line) for line in text.splitlines() if line.strip()]


def demux_logs(payload: bytes) -> list[tuple[str, str]]:
    """Split a Docker log payload into (stream, line) pairs.

    Docker multiplexes non-TTY output into frames of an 8-byte header
    (stream byte, three zero bytes, big-endian length) plus payload.
    TTY containers stream raw bytes instead, so anything that does not
    parse as frames is treated as raw stdout.

    Args:
        payload: Response body of the container logs endpoint.

    Returns:
        Lines labeled with the stream they came from.
    """
    lines: list[tuple[str, str]] = []
    offset = 0
    total = len(payload)
    framed = False
    while offset + _FRAME_HEADER_SIZE <= total:
        stream_byte = payload[offset]
        header = payload[offset : offset + _FRAME_HEADER_SIZE]
        if stream_byte not in (1, 2) or header[1:4] != b"\x00\x00\x00":
            break
        length = int.from_bytes(header[4:8], "big")
        end = offset + _FRAME_HEADER_SIZE + length
        if end > total:
            break
        chunk = payload[offset + _FRAME_HEADER_SIZE : end]
        stream = "stderr" if stream_byte == 2 else "stdout"
        lines.extend(_decode_chunk(stream, chunk))
        framed = True
        offset = end
    if framed:
        return lines
    return _decode_chunk("stdout", payload)


def _format_ports(ports: list[dict[str, Any]]) -> list[str]:
    """Render a container port list as short human strings.

    Args:
        ports: Port objects from the Docker containers list.

    Returns:
        Strings like ``5434->5432/tcp`` or ``8014/tcp``.
    """
    rendered: list[str] = []
    for port in ports:
        private = port.get("PrivatePort")
        if private is None:
            continue
        public = port.get("PublicPort")
        protocol = port.get("Type", "tcp")
        if public:
            rendered.append(f"{public}->{private}/{protocol}")
        else:
            rendered.append(f"{private}/{protocol}")
    return rendered


def _parse_container(summary: dict[str, Any]) -> ContainerInfo:
    """Build a ContainerInfo from a containers-list entry.

    Args:
        summary: One object of ``GET /containers/json``.

    Returns:
        Normalized container information.
    """
    labels = summary.get("Labels") or {}
    names = summary.get("Names") or []
    fallback_name = names[0].lstrip("/") if names else "unknown"
    service = labels.get("com.docker.compose.service") or fallback_name
    return ContainerInfo(
        id=str(summary.get("Id") or ""),
        name=fallback_name,
        service=service,
        image=str(summary.get("Image") or ""),
        state=str(summary.get("State") or "unknown"),
        status=str(summary.get("Status") or ""),
        ports=tuple(_format_ports(summary.get("Ports") or [])),
    )


def _apply_inspect(
    info: ContainerInfo, payload: dict[str, Any]
) -> ContainerInfo:
    """Merge allowlisted inspect fields into a container info.

    Only state, health, start time and restart count are read; the
    environment, command line and mount sources are never touched.

    Args:
        info: Container info parsed from the list endpoint.
        payload: Response of ``GET /containers/{id}/json``.

    Returns:
        Info enriched with health, started_at and restart_count.
    """
    state = payload.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    restarts = payload.get("RestartCount")
    return replace(
        info,
        state=str(state.get("Status") or info.state),
        health=str(health) if health else None,
        started_at=_parse_rfc3339(str(state.get("StartedAt") or "")),
        restart_count=restarts if isinstance(restarts, int) else None,
    )


class DockerCollector:
    """Read container state and logs through the socket proxy."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        project: str,
    ) -> None:
        """Configure the collector.

        Args:
            client: Shared httpx client (tests may inject a MockTransport).
            base_url: Base URL of the Docker socket proxy.
            project: Compose project label used to select containers.
        """
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._project = project

    async def _request(self, path: str, **kwargs: Any) -> httpx.Response:
        """Perform a GET request against the proxy.

        Args:
            path: Path beginning with a slash.
            **kwargs: Extra arguments for ``httpx.AsyncClient.get``.

        Returns:
            Raw response object.

        Raises:
            RuntimeError: The proxy answered with a non-200 status.
        """
        response = await self._client.get(f"{self._base_url}{path}", **kwargs)
        if response.status_code != 200:
            raise RuntimeError(
                f"Docker API {path} returned {response.status_code}"
            )
        return response

    async def ping(self) -> bool:
        """Check that the socket proxy answers ``/_ping``.

        Returns:
            True when the proxy is reachable.
        """
        try:
            response = await self._request("/_ping")
        except Exception:
            return False
        return response.text.strip() == "OK"

    async def get_containers(self) -> list[ContainerInfo]:
        """List containers of the compose project with inspected state.

        Returns:
            Containers sorted by compose service name.
        """
        filters = json.dumps(
            {"label": [f"com.docker.compose.project={self._project}"]}
        )
        response = await self._request(
            "/containers/json", params={"all": "1", "filters": filters}
        )
        summaries = response.json() or []
        containers = [_parse_container(item) for item in summaries]
        inspected = await asyncio.gather(
            *(self._inspect(container) for container in containers)
        )
        return sorted(inspected, key=lambda item: item.service)

    async def _inspect(self, info: ContainerInfo) -> ContainerInfo:
        """Fetch allowlisted inspect fields for one container.

        Inspect failures do not fail the whole list: the container is
        returned with list data only.

        Args:
            info: Container parsed from the list endpoint.

        Returns:
            Enriched container info, unchanged on failure.
        """
        path = f"/containers/{quote(info.id, safe='')}/json"
        try:
            response = await self._request(path)
            return _apply_inspect(info, response.json() or {})
        except Exception:
            return info

    async def get_logs(
        self, container_id: str, tail: int = 200
    ) -> list[ContainerLogLine]:
        """Fetch the last lines of a container's logs.

        Args:
            container_id: Container identifier from the list endpoint.
            tail: Number of lines to request (clamped to 1..500).

        Returns:
            Parsed log lines in chronological order.
        """
        bounded_tail = max(1, min(tail, _MAX_TAIL))
        path = f"/containers/{quote(container_id, safe='')}/logs"
        response = await self._request(
            path,
            params={
                "stdout": "1",
                "stderr": "1",
                "timestamps": "1",
                "tail": str(bounded_tail),
            },
        )
        lines: list[ContainerLogLine] = []
        for stream, raw in demux_logs(response.content):
            timestamp, message = _split_timestamp(raw)
            lines.append(
                ContainerLogLine(
                    timestamp=timestamp,
                    stream=stream,
                    message=message,
                )
            )
        return lines
