"""Tail the application JSONL log files from the shared volume."""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LOG_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}\.jsonl$")
_TAIL_BYTES = 512 * 1024
_MAX_LIMIT = 500
_EXCEPTION_LIMIT = 4000


@dataclass(frozen=True, slots=True)
class LogFileInfo:
    """Metadata of one JSONL log file."""

    name: str
    size_bytes: int
    modified_at: datetime


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One parsed JSONL record."""

    timestamp: str
    level: str
    logger: str
    message: str
    exception: str | None = None


@dataclass(frozen=True, slots=True)
class LogSlice:
    """Tail of one log file after filtering."""

    file: str
    entries: list[LogEntry]
    truncated: bool
    scanned: int


def _list_files_sync(root: Path) -> list[LogFileInfo]:
    """List valid log files of the directory, newest first.

    Args:
        root: Log directory.

    Returns:
        File metadata; symlinks and foreign names are skipped.
    """
    if not root.is_dir():
        return []
    files: list[LogFileInfo] = []
    for entry in root.iterdir():
        if not LOG_NAME_RE.match(entry.name):
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            stat = entry.stat()
        except OSError:
            continue
        files.append(
            LogFileInfo(
                name=entry.name,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
            )
        )
    return sorted(files, key=lambda item: item.name, reverse=True)


def _parse_entry(raw: str) -> LogEntry | None:
    """Parse one JSONL line into a LogEntry.

    Args:
        raw: Raw line from the log file.

    Returns:
        Parsed entry, or None for malformed lines.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    exception = data.get("exception")
    return LogEntry(
        timestamp=str(data.get("timestamp") or ""),
        level=str(data.get("level") or "?"),
        logger=str(data.get("full_module") or ""),
        message=str(data.get("message") or ""),
        exception=(str(exception)[:_EXCEPTION_LIMIT] if exception else None),
    )


def _read_tail_sync(
    root: Path,
    filename: str,
    level: str | None,
    query: str | None,
    limit: int,
) -> LogSlice:
    """Read and filter the last chunk of a log file.

    Args:
        root: Log directory.
        filename: File name already validated against the whitelist.
        level: Exact level filter (uppercase) or None.
        query: Case-insensitive substring over message and logger.
        limit: Maximum number of entries to return.

    Returns:
        Filtered entries in chronological order.

    Raises:
        ValueError: The path escapes the directory or is not a file.
    """
    path = root / filename
    resolved = path.resolve()
    if resolved.parent != root.resolve() or not resolved.is_file():
        raise ValueError("log file is outside the log directory")
    if path.is_symlink():
        raise ValueError("log file is a symlink")
    size = resolved.stat().st_size
    start = max(0, size - _TAIL_BYTES)
    with resolved.open("rb") as handle:
        if start:
            handle.seek(start)
        payload = handle.read()
    lines = payload.decode("utf-8", errors="replace").splitlines()
    if start and lines:
        lines = lines[1:]
    needle = query.lower() if query else None
    entries: list[LogEntry] = []
    for line in lines:
        entry = _parse_entry(line)
        if entry is None:
            continue
        if level and entry.level.upper() != level.upper():
            continue
        if needle and (
            needle not in entry.message.lower()
            and needle not in entry.logger.lower()
        ):
            continue
        entries.append(entry)
    return LogSlice(
        file=filename,
        entries=entries[-limit:],
        truncated=bool(start),
        scanned=len(lines),
    )


class LogsCollector:
    """Serve log file listings and filtered tails."""

    def __init__(self, log_dir: Path) -> None:
        """Configure the collector.

        Args:
            log_dir: Directory holding the JSONL files.
        """
        self._root = log_dir

    async def list_files(self) -> list[LogFileInfo]:
        """List log files available on the shared volume.

        Returns:
            File metadata, newest first.
        """
        return await asyncio.to_thread(_list_files_sync, self._root)

    async def tail(
        self,
        filename: str,
        level: str | None = None,
        query: str | None = None,
        limit: int = 200,
    ) -> LogSlice:
        """Return the filtered tail of one log file.

        Args:
            filename: Name of the file; must match the whitelist.
            level: Exact level filter, e.g. ``ERROR``.
            query: Case-insensitive substring filter.
            limit: Maximum entries to return (clamped to 1..500).

        Returns:
            Filtered log slice.

        Raises:
            ValueError: The file name fails the whitelist check.
        """
        if not LOG_NAME_RE.match(filename):
            raise ValueError("invalid log file name")
        bounded = max(1, min(limit, _MAX_LIMIT))
        return await asyncio.to_thread(
            _read_tail_sync,
            self._root,
            filename,
            level,
            query,
            bounded,
        )
