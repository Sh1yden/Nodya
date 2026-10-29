"""Unit tests for the JSONL log collector."""

import json
from pathlib import Path

import pytest

from app.admin import collectors
from app.admin.collectors.logs import LogsCollector


def _write(path: Path, entries: list[dict[str, str]]) -> None:
    path.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )


def _entry(
    message: str, level: str = "INFO", logger: str = "nodya.worker"
) -> dict[str, str]:
    return {
        "timestamp": "12:00:00.123",
        "level": level,
        "full_module": logger,
        "message": message,
    }


async def test_list_files_filters_and_sorts(tmp_path: Path) -> None:
    _write(tmp_path / "2026-10-29-01.jsonl", [_entry("first")])
    _write(tmp_path / "2026-10-30-01.jsonl", [_entry("second")])
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "bad.jsonl").write_text("x", encoding="utf-8")
    files = await LogsCollector(tmp_path).list_files()
    assert [item.name for item in files] == [
        "2026-10-30-01.jsonl",
        "2026-10-29-01.jsonl",
    ]
    assert files[0].size_bytes > 0


async def test_tail_returns_entries_with_filters(tmp_path: Path) -> None:
    _write(
        tmp_path / "2026-10-29-01.jsonl",
        [
            _entry("all good"),
            _entry("boom happened", level="ERROR"),
            _entry("problem here", level="ERROR", logger="nodya.feed"),
            _entry("boom again", level="ERROR"),
        ],
    )
    collector = LogsCollector(tmp_path)
    slice_ = await collector.tail("2026-10-29-01.jsonl")
    assert [entry.message for entry in slice_.entries] == [
        "all good",
        "boom happened",
        "problem here",
        "boom again",
    ]
    assert slice_.scanned == 4
    errors = await collector.tail("2026-10-29-01.jsonl", level="error")
    assert len(errors.entries) == 3
    matching = await collector.tail("2026-10-29-01.jsonl", query="BOOM")
    assert [entry.message for entry in matching.entries] == [
        "boom happened",
        "boom again",
    ]
    by_logger = await collector.tail("2026-10-29-01.jsonl", query="nodya.feed")
    assert [entry.message for entry in by_logger.entries] == ["problem here"]
    limited = await collector.tail("2026-10-29-01.jsonl", limit=2)
    assert [entry.message for entry in limited.entries] == [
        "problem here",
        "boom again",
    ]


async def test_tail_reads_only_the_file_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collectors.logs, "_TAIL_BYTES", 200)
    _write(
        tmp_path / "2026-10-29-01.jsonl",
        [_entry(f"message number {index}") for index in range(20)],
    )
    slice_ = await LogsCollector(tmp_path).tail("2026-10-29-01.jsonl")
    assert slice_.truncated
    assert slice_.scanned < 20
    assert slice_.entries[-1].message == "message number 19"


async def test_tail_rejects_foreign_names(tmp_path: Path) -> None:
    collector = LogsCollector(tmp_path)
    with pytest.raises(ValueError):
        await collector.tail("../secret.jsonl")
    with pytest.raises(ValueError):
        await collector.tail("2026-10-29-01.txt")
    with pytest.raises(ValueError):
        await collector.tail("")


async def test_tail_rejects_symlink_and_missing_file(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "outside-log.jsonl"
    _write(outside, [_entry("secret")])
    link = tmp_path / "2026-10-29-02.jsonl"
    link.symlink_to(outside)
    collector = LogsCollector(tmp_path)
    with pytest.raises(ValueError):
        await collector.tail(link.name)
    with pytest.raises(ValueError):
        await collector.tail("2026-10-29-03.jsonl")
    outside.unlink()


async def test_tail_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "2026-10-29-01.jsonl"
    path.write_text(
        'not json\n{"level": "INFO", "message": "ok"}\n',
        encoding="utf-8",
    )
    slice_ = await LogsCollector(tmp_path).tail(path.name)
    assert [entry.message for entry in slice_.entries] == ["ok"]
    assert slice_.entries[0].logger == ""
