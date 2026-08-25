"""Logging setup: colored console + JSONL file output."""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import ClassVar


def setup_logging(
    level: str = "DEBUG",
    log_dir: Path = Path("logs"),
    console: bool = True,
    file: bool = True,
) -> None:
    """Configure the root application logger once at startup.

    Args:
        level: Root log level name (DEBUG, INFO, ...).
        log_dir: Directory for JSONL log files.
        console: Attach a colored stdout handler.
        file: Attach a JSONL file handler.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("nodya")
    root_logger.setLevel(getattr(logging, level.upper()))
    root_logger.handlers.clear()

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(ColoredConsoleFormatter())
        root_logger.addHandler(console_handler)

    if file:
        log_filename = f"{datetime.now().strftime('%Y-%m-%d')}-01.jsonl"
        log_filepath = log_dir / log_filename

        # Find a free file index for today
        counter = 1
        while log_filepath.exists():
            counter += 1
            log_filename = (
                f"{datetime.now().strftime('%Y-%m-%d')}-{counter:02d}.jsonl"
            )
            log_filepath = log_dir / log_filename

        file_handler = logging.FileHandler(log_filepath, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JSONFormatter())
        root_logger.addHandler(file_handler)

    # Do not propagate into the Python root logger
    root_logger.propagate = False

    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


class Colors:
    """ANSI escape codes used by the console formatter."""

    RESET = "\033[0m"

    CURRENT_TIME_COLOR = "\u001b[34;1m"
    FILENAME_COLOR = "\u001b[32m"
    MODULE_COLOR = "\u001b[33m"
    CLASS_COLOR = "\u001b[34m"
    DEF_COLOR = "\u001b[36m"
    MESSAGE_COLOR = "\u001b[37m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    BRIGHT_RED = "\033[91m"


class ColoredConsoleFormatter(logging.Formatter):
    """Human-readable colored formatter for stdout."""

    LEVEL_COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": Colors.CYAN,
        "INFO": Colors.GREEN,
        "WARNING": Colors.YELLOW,
        "ERROR": Colors.RED,
        "CRITICAL": Colors.BRIGHT_RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        """Render a record as `time | LEVEL | file | module | func | msg`.

        Args:
            record: Log record to render.

        Returns:
            Colored single-line string.
        """
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        filename = (
            os.path.basename(record.pathname) if record.pathname else None
        )
        color = self.LEVEL_COLORS.get(record.levelname, Colors.RESET)

        return (
            f"{Colors.CURRENT_TIME_COLOR}{current_time}{Colors.RESET} | "
            f"{color}{record.levelname:<8}{Colors.RESET} | "
            f"{Colors.FILENAME_COLOR}{filename}{Colors.RESET} | "
            f"{Colors.MODULE_COLOR}{record.name}{Colors.RESET} | "
            f"{Colors.DEF_COLOR}{record.funcName}{Colors.RESET} | "
            f"{record.getMessage()}"
        )


class JSONFormatter(logging.Formatter):
    """Machine-readable JSONL formatter for log files."""

    def format(self, record: logging.LogRecord) -> str:
        """Render a record as a compact JSON object.

        Args:
            record: Log record to render.

        Returns:
            JSON string with timestamp, level, location and message.
            Includes traceback under "exception" when present.
        """
        filename = (
            os.path.basename(record.pathname) if record.pathname else None
        )

        log_entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "level": record.levelname,
            "filename": filename,
            "full_module": record.name,
            "function": record.funcName,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)
