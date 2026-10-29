"""Unit tests for ``app.core.metrics``."""

from __future__ import annotations

import pytest

from app.core.metrics import start_metrics_server


def test_start_metrics_server_handles_bind_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(port: int) -> None:
        raise OSError("port busy")

    monkeypatch.setattr("app.core.metrics.start_http_server", boom)

    assert start_metrics_server(8015) is False


def test_start_metrics_server_passes_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("app.core.metrics.start_http_server", calls.append)

    assert start_metrics_server(8015) is True
    assert calls == [8015]
