"""Retention cleanup for JSONL application logs."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core import get_logger

logger = get_logger(__name__)

_LOG_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}\.jsonl$")


def prune_logs(log_dir: Path, days: int, now: datetime | None = None) -> int:
    """Delete Nodya JSONL files older than the retention window.

    Only files matching the logger's own naming scheme are touched;
    symlinks are skipped, so a hostile link cannot redirect the
    deletion outside the directory.

    Args:
        log_dir: Directory with ``YYYY-MM-DD-NN.jsonl`` files.
        days: Retention window in days; <= 0 disables pruning.
        now: Reference time (tests); defaults to the current UTC time.

    Returns:
        Number of deleted files.
    """
    if days <= 0:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    deleted = 0
    for path in log_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        if not _LOG_NAME_RE.match(path.name):
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified >= cutoff:
            continue
        try:
            path.unlink()
        except OSError as exc:
            logger.warning(f"Log prune failed for {path.name}: {exc}")
            continue
        deleted += 1
    if deleted:
        logger.info(f"Log retention removed {deleted} file(s).")
    return deleted
