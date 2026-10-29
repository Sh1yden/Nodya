"""Prometheus metrics and the internal metrics HTTP server."""

from prometheus_client import Counter, Histogram, Info, start_http_server

from app.core import get_logger

logger = get_logger(__name__)

WEBHOOK_UPDATES = Counter(
    "nodya_webhook_updates_total",
    "Telegram webhook updates by processing result.",
    ["result"],
)
WORKER_BATCHES = Counter(
    "nodya_worker_batches_total",
    "Processed debounce batches by result.",
    ["result"],
)
WORKER_MESSAGES = Counter(
    "nodya_worker_messages_total",
    "Worker messages by direction.",
    ["direction"],
)
LLM_REQUESTS = Counter(
    "nodya_llm_requests_total",
    "LLM requests by provider, model and status.",
    ["provider", "model", "status"],
)
LLM_LATENCY = Histogram(
    "nodya_llm_request_seconds",
    "LLM request latency by provider and model.",
    ["provider", "model"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)
SCHEDULER_RUNS = Counter(
    "nodya_scheduler_runs_total",
    "Scheduler job runs by job id and result.",
    ["job", "result"],
)
ERROR_SINK_EVENTS = Counter(
    "nodya_error_sink_events_total",
    "Error sink occurrences by result.",
    ["result"],
)
APP_INFO = Info("nodya_app", "Application build information.")


def start_metrics_server(port: int) -> bool:
    """Expose the default registry over HTTP on an internal port.

    Metrics must never take the application down: a busy port or any
    other OSError is logged and swallowed.

    Args:
        port: TCP port for the daemon-thread Prometheus server.

    Returns:
        True when the server started, False on bind failure.
    """
    try:
        start_http_server(port)
    except OSError as exc:
        logger.error(f"Metrics server failed on port {port}: {exc}")
        return False
    logger.info(f"Metrics server listening on :{port}.")
    return True
