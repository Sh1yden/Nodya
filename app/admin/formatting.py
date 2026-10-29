"""Jinja filters used by the admin templates."""

from datetime import UTC, datetime
from typing import Any

_OK_STATES = {"running", "healthy", "ok", "ready", "up", "success"}
_WARN_STATES = {
    "restarting",
    "paused",
    "starting",
    "stale",
    "warning",
    "degraded",
}
_BAD_STATES = {
    "exited",
    "dead",
    "unhealthy",
    "missing",
    "error",
    "critical",
    "down",
    "failed",
}


def format_datetime(value: datetime | None) -> str:
    """Render a datetime in panel-local (host UTC) form.

    Args:
        value: Datetime or None.

    Returns:
        ``YYYY-MM-DD HH:MM:SS`` or an em dash.
    """
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_iso(value: str | None) -> str:
    """Render an ISO timestamp string in compact form.

    Args:
        value: ISO-8601 string from the heartbeat payload.

    Returns:
        ``YYYY-MM-DD HH:MM:SS``, the raw value when unparsable,
        or an em dash when empty.
    """
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return value
    return stamp.strftime("%Y-%m-%d %H:%M:%S")


def format_uptime(seconds: float | None) -> str:
    """Humanize a duration in seconds.

    Args:
        seconds: Duration or None.

    Returns:
        Compact Russian label such as ``2 д 3 ч`` or ``5 мин 12 с``.
    """
    if seconds is None:
        return "—"
    total = int(max(0.0, seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"


def format_ago(value: datetime | None) -> str:
    """Render how long ago a datetime was.

    Args:
        value: Datetime or None.

    Returns:
        ``5 мин назад`` style label or an em dash.
    """
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - value).total_seconds()
    if age <= 1:
        return "только что"
    return f"{format_uptime(age)} назад"


def format_bytes(value: int | None) -> str:
    """Render a byte count with binary units.

    Args:
        value: Number of bytes or None.

    Returns:
        ``12.3 МиБ`` style label.
    """
    if value is None:
        return "—"
    size = float(max(0, value))
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if size < 1024 or unit == "ТиБ":
            return (
                f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
            )
        size /= 1024
    return f"{size:.1f} ТиБ"


def status_class(value: str | None) -> str:
    """Map a state or level name to a CSS status class.

    Args:
        value: State name such as ``healthy`` or ``ERROR``.

    Returns:
        One of ``ok``, ``warn``, ``bad``, ``muted``.
    """
    normalized = (value or "").strip().lower()
    if normalized in _OK_STATES:
        return "ok"
    if normalized in _WARN_STATES:
        return "warn"
    if normalized in _BAD_STATES:
        return "bad"
    return "muted"


def container_class(container: Any) -> str:
    """Status class for a container card or row.

    Args:
        container: Object with ``state`` and ``health`` attributes.

    Returns:
        CSS status class.
    """
    state = (container.state or "").lower()
    health = (container.health or "").lower()
    if state == "running":
        if health == "unhealthy":
            return "bad"
        if health in {"starting", "pause"}:
            return "warn"
        return "ok"
    if state in {"restarting", "paused", "created"}:
        return "warn"
    return "bad"


def level_class(level: str | None) -> str:
    """Status class for a log level.

    Args:
        level: Level name such as ``ERROR``.

    Returns:
        CSS status class; ERROR is highlighted stronger than WARNING.
    """
    normalized = (level or "").upper()
    if normalized == "CRITICAL":
        return "bad"
    if normalized == "ERROR":
        return "bad"
    if normalized == "WARNING":
        return "warn"
    if normalized == "INFO":
        return "ok"
    return "muted"
