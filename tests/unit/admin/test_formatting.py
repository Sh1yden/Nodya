"""Unit tests for the admin panel Jinja filters."""

from datetime import UTC, datetime, timedelta

from app.admin.collectors.docker import ContainerInfo
from app.admin.formatting import (
    container_class,
    format_ago,
    format_bytes,
    format_datetime,
    format_iso,
    format_uptime,
    level_class,
    status_class,
)


def _container(state: str, health: str | None = None) -> ContainerInfo:
    return ContainerInfo(
        id="abcdef1234567890",
        name="nodya_heart",
        service="app",
        image="nodya-app:local",
        state=state,
        status="",
        health=health,
    )


def test_format_uptime_scales() -> None:
    assert format_uptime(None) == "—"
    assert format_uptime(0) == "0 с"
    assert format_uptime(59) == "59 с"
    assert format_uptime(61) == "1 мин 1 с"
    assert format_uptime(3661) == "1 ч 1 мин"
    assert format_uptime(90061) == "1 д 1 ч"
    assert format_uptime(-5) == "0 с"


def test_format_bytes_binary_units() -> None:
    assert format_bytes(None) == "—"
    assert format_bytes(0) == "0 Б"
    assert format_bytes(1023) == "1023 Б"
    assert format_bytes(1024) == "1.0 КиБ"
    assert format_bytes(5 * 1024 * 1024) == "5.0 МиБ"
    assert format_bytes(3 * 1024**3) == "3.0 ГиБ"


def test_format_datetime_and_iso() -> None:
    moment = datetime(2026, 10, 29, 12, 0, 5, tzinfo=UTC)
    assert format_datetime(moment) == "2026-10-29 12:00:05"
    assert format_datetime(None) == "—"
    assert format_iso(moment.isoformat()) == "2026-10-29 12:00:05"
    assert format_iso("garbage") == "garbage"
    assert format_iso(None) == "—"


def test_format_ago() -> None:
    now = datetime.now(UTC)
    assert format_ago(None) == "—"
    assert format_ago(now) == "только что"
    assert format_ago(now - timedelta(minutes=5)) == "5 мин 0 с назад"


def test_status_class() -> None:
    assert status_class("healthy") == "ok"
    assert status_class("running") == "ok"
    assert status_class("stale") == "warn"
    assert status_class("unhealthy") == "bad"
    assert status_class("exited") == "bad"
    assert status_class(None) == "muted"
    assert status_class("whatever") == "muted"


def test_container_class() -> None:
    assert container_class(_container("running", "healthy")) == "ok"
    assert container_class(_container("running")) == "ok"
    assert container_class(_container("running", "unhealthy")) == "bad"
    assert container_class(_container("running", "starting")) == "warn"
    assert container_class(_container("restarting")) == "warn"
    assert container_class(_container("exited")) == "bad"
    assert container_class(_container("created")) == "warn"


def test_level_class() -> None:
    assert level_class("CRITICAL") == "bad"
    assert level_class("error") == "bad"
    assert level_class("WARNING") == "warn"
    assert level_class("INFO") == "ok"
    assert level_class("DEBUG") == "muted"
    assert level_class(None) == "muted"
