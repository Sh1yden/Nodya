"""Unit tests for ``app.core.log_retention``."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.log_retention import prune_logs

_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def _age(path: Path, days: int) -> None:
    stamp = (_NOW - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def test_prunes_only_old_matching_files(tmp_path: Path) -> None:
    old = tmp_path / "2026-10-01-01.jsonl"
    fresh = tmp_path / "2026-10-28-01.jsonl"
    foreign = tmp_path / "notes.jsonl"
    for path in (old, fresh, foreign):
        path.write_text("{}\n", encoding="utf-8")
    _age(old, 20)
    _age(fresh, 1)
    _age(foreign, 90)

    deleted = prune_logs(tmp_path, days=14, now=_NOW)

    assert deleted == 1
    assert not old.exists()
    assert fresh.exists()
    assert foreign.exists()


def test_skips_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "2026-01-01-01.jsonl"
    link.symlink_to(target)
    _age(target, 90)

    assert prune_logs(tmp_path, days=14, now=_NOW) == 0
    assert link.is_symlink()
    assert target.exists()


def test_disabled_window_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "2026-01-01-01.jsonl"
    path.write_text("x", encoding="utf-8")
    _age(path, 90)

    assert prune_logs(tmp_path, days=0, now=_NOW) == 0
    assert path.exists()
