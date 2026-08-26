"""Unit tests for ``app.core.logger``."""

from __future__ import annotations

import logging

from app.core.logger import LoggerMixin, get_logger


class TestGetLogger:
    """Test get_logger name resolution."""

    def test_none_name_returns_caller(self) -> None:
        logger = get_logger()
        assert isinstance(logger, logging.Logger)

    def test_explicit_name_prepends_prefix(self) -> None:
        logger = get_logger("mypackage.mymodule")
        assert logger.name == "nodya.mypackage.mymodule"

    def test_already_prefixed_not_doubled(self) -> None:
        logger = get_logger("nodya.already")
        assert logger.name == "nodya.already"

    def test_main_mapping(self) -> None:
        logger = get_logger("__main__")
        assert logger.name == "nodya"

    def test_main_dot_mapping(self) -> None:
        logger = get_logger("__main__.something")
        assert logger.name == "nodya.something"


class TestLoggerMixin:
    """Test ``LoggerMixin._lg`` property."""

    def test_returns_logger(self) -> None:
        class MyClass(LoggerMixin):
            pass

        obj = MyClass()
        assert isinstance(obj._lg, logging.Logger)

    def test_name_contains_class_name(self) -> None:
        class MySpecialClass(LoggerMixin):
            pass

        obj = MySpecialClass()
        assert "MySpecialClass" in obj._lg.name

    def test_caches_logger_instance(self) -> None:
        class CachedClass(LoggerMixin):
            pass

        obj = CachedClass()
        first = obj._lg
        second = obj._lg
        assert first is second
