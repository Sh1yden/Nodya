"""Error capture pipeline: logging handler -> asyncio queue -> PostgreSQL.

Records of ``nodya.*`` loggers at ERROR and above are grouped by a
stable fingerprint and upserted into ``error_events`` by a background
drainer, so the admin panel can show recurring failures with counters
instead of a raw log tail.
"""

import asyncio
import hashlib
import logging
import re
import time
import traceback
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from app.brain.repositories import ErrorEventsRepo, ErrorEventUpsert
from app.core import get_logger
from app.core.metrics import ERROR_SINK_EVENTS

logger = get_logger(__name__)

_OWN_LOGGER = __name__
_MESSAGE_LIMIT = 2000
_TRACEBACK_LIMIT = 8000
_NORMALIZED_LIMIT = 500
_BATCH_SIZE = 100
_BATCH_WINDOW_SECONDS = 2.0
_DB_TIMEOUT_SECONDS = 5.0
_STOP_TIMEOUT_SECONDS = 10.0
_FLUSH_WARNING_INTERVAL_SECONDS = 60.0
_QUEUE_SIZE = 1000

_STOP: object = object()

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_NUMBER_RE = re.compile(r"\b\d+\b")
_SECRET_KV_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)"
    r"\s*[=:]\s*\S+"
)
_BOT_TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*")
_WHITESPACE_RE = re.compile(r"\s+")


def redact_secrets(text: str) -> str:
    """Mask credential-looking fragments of a message or traceback.

    Args:
        text: Raw log message or formatted traceback.

    Returns:
        Text with secrets replaced by ``<redacted>``.
    """
    cleaned = _BOT_TOKEN_RE.sub("<redacted>", text)
    cleaned = _BEARER_RE.sub("bearer <redacted>", cleaned)
    return _SECRET_KV_RE.sub(r"\1=<redacted>", cleaned)


def normalize_message(message: str) -> str:
    """Collapse volatile parts so equal errors share a fingerprint.

    UUIDs, long hex ids, numbers and whitespace change between
    occurrences; secrets are masked first.

    Args:
        message: Log message to normalize.

    Returns:
        Normalized message capped at 500 characters.
    """
    text = redact_secrets(message)
    text = _UUID_RE.sub("<uuid>", text)
    text = _HEX_RE.sub("<hex>", text)
    text = _NUMBER_RE.sub("<n>", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text[:_NORMALIZED_LIMIT]


def make_fingerprint(
    logger_name: str,
    function: str,
    exception_type: str | None,
    message: str,
) -> str:
    """Build the stable sha256 group id of an error.

    Args:
        logger_name: Logger name of the record.
        function: Function where the record was emitted.
        exception_type: Exception class name, or None without exc_info.
        message: Raw log message.

    Returns:
        64-character hex digest.
    """
    raw = "|".join(
        (
            logger_name,
            function,
            exception_type or "",
            normalize_message(message),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def aggregate_events(
    events: list[ErrorEventUpsert],
) -> list[ErrorEventUpsert]:
    """Merge occurrences of the same fingerprint into one row.

    Args:
        events: Raw occurrences collected from the queue.

    Returns:
        One upsert item per fingerprint with summed counters.
    """
    grouped: dict[str, ErrorEventUpsert] = {}
    for event in events:
        existing = grouped.get(event.fingerprint)
        if existing is None:
            grouped[event.fingerprint] = event
            continue
        grouped[event.fingerprint] = ErrorEventUpsert(
            fingerprint=event.fingerprint,
            level=event.level,
            logger=event.logger,
            module=event.module,
            function=event.function,
            message=event.message,
            seen_at=max(existing.seen_at, event.seen_at),
            exception_type=event.exception_type or existing.exception_type,
            traceback=event.traceback or existing.traceback,
            count=existing.count + event.count,
        )
    return list(grouped.values())


class ErrorEventSink(logging.Handler):
    """Capture nodya ERROR records and persist them in batches."""

    def __init__(
        self,
        session_factory: Any,
        queue_size: int = _QUEUE_SIZE,
    ) -> None:
        """Create an inactive handler (call start() inside the loop).

        Args:
            session_factory: Async session factory for ``error_events``.
            queue_size: Bounded queue capacity; overflow is dropped.
        """
        super().__init__(level=logging.ERROR)
        self._session_factory = session_factory
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_size)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._last_flush_warning = 0.0

    def start(self) -> None:
        """Bind to the running loop and spawn the drainer task."""
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(self._run(), name="error-sink")

    async def stop(self) -> None:
        """Flush what is queued and stop the drainer."""
        task, self._task = self._task, None
        self._loop = None
        if task is not None:
            try:
                self._queue.put_nowait(_STOP)
            except asyncio.QueueFull:
                self._queue.get_nowait()
                self._queue.put_nowait(_STOP)
            try:
                async with asyncio.timeout(_STOP_TIMEOUT_SECONDS):
                    await task
            except TimeoutError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                logger.warning("Error sink drain timed out.")
        leftovers = self._drain_queue()
        if leftovers:
            await self._flush(leftovers)

    def emit(self, record: logging.LogRecord) -> None:
        """Enqueue a record without doing any I/O in the logger thread.

        Args:
            record: Log record produced by the logging framework.
        """
        if record.levelno < logging.ERROR:
            return
        name = record.name
        if name != "nodya" and not name.startswith("nodya."):
            return
        if name == _OWN_LOGGER:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            payload = self._to_event(record)
        except Exception:
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, payload)
        except RuntimeError:
            return

    def _enqueue(self, payload: ErrorEventUpsert) -> None:
        """Put a payload into the bounded queue or count a drop."""
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            ERROR_SINK_EVENTS.labels(result="dropped").inc()

    async def _run(self) -> None:
        """Drain the queue in batches until the stop sentinel arrives."""
        while True:
            first = await self._queue.get()
            if first is _STOP:
                return
            batch = [first]
            deadline = time.monotonic() + _BATCH_WINDOW_SECONDS
            while len(batch) < _BATCH_SIZE:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), remaining)
                except TimeoutError:
                    break
                if item is _STOP:
                    await self._flush(batch)
                    return
                batch.append(item)
            await self._flush(batch)

    async def _flush(self, events: list[ErrorEventUpsert]) -> None:
        """Upsert one aggregated batch; failures drop it loudly once.

        Args:
            events: Occurrences collected from the queue.
        """
        grouped = aggregate_events(events)
        try:
            async with asyncio.timeout(_DB_TIMEOUT_SECONDS):
                async with self._session_factory() as session:
                    await ErrorEventsRepo(session).upsert_many(grouped)
                    await session.commit()
        except Exception as exc:
            ERROR_SINK_EVENTS.labels(result="flush_failed").inc()
            self._warn_flush_failure(exc)
            return
        ERROR_SINK_EVENTS.labels(result="captured").inc(len(events))

    def _warn_flush_failure(self, exc: Exception) -> None:
        """Log a rate-limited warning when PostgreSQL is unavailable."""
        now = time.monotonic()
        if now - self._last_flush_warning < _FLUSH_WARNING_INTERVAL_SECONDS:
            return
        self._last_flush_warning = now
        logger.warning(
            f"Error sink flush failed ({type(exc).__name__}); batch dropped."
        )

    def _drain_queue(self) -> list[ErrorEventUpsert]:
        """Remove and return everything currently in the queue."""
        items: list[Any] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return [item for item in items if item is not _STOP]

    @staticmethod
    def _to_event(record: logging.LogRecord) -> ErrorEventUpsert:
        """Convert a log record into an upsert item with redaction."""
        exception_type: str | None = None
        traceback_text: str | None = None
        if record.exc_info and record.exc_info[0] is not None:
            exception_type = record.exc_info[0].__name__
            traceback_text = redact_secrets(
                "".join(traceback.format_exception(*record.exc_info))
            )[:_TRACEBACK_LIMIT]
        message = redact_secrets(record.getMessage())[:_MESSAGE_LIMIT]
        function = record.funcName or "unknown"
        return ErrorEventUpsert(
            fingerprint=make_fingerprint(
                record.name or "", function, exception_type, message
            ),
            level=record.levelname,
            logger=(record.name or "")[:255],
            module=(record.module or "")[:255],
            function=function[:255],
            message=message,
            seen_at=datetime.now(UTC),
            exception_type=exception_type,
            traceback=traceback_text,
        )
