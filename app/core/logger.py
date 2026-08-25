"""Logger factory and mixin with the `nodya` namespace."""

import inspect
import logging

_ROOT_PREFIX = "nodya"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a namespaced logger for a module of the monolith.

    Args:
        name: Logger name. When None, the caller module `__name__`
            is used. `__main__` is mapped to the root prefix.

    Returns:
        logging.Logger under the "nodya" hierarchy.
    """
    if name is None:
        frame = inspect.currentframe()
        if frame and frame.f_back:
            name = frame.f_back.f_globals.get("__name__", "unknown")

    if name == "__main__":
        name = _ROOT_PREFIX
    elif name and not name.startswith(f"{_ROOT_PREFIX}."):
        if name.startswith("__main__."):
            name = name.replace("__main__.", f"{_ROOT_PREFIX}.", 1)
        else:
            name = f"{_ROOT_PREFIX}.{name}"

    return logging.getLogger(name)


class LoggerMixin:
    """Mixin that lazily binds a class-scoped logger via `_lg`."""

    @property
    def _lg(self) -> logging.Logger:
        """Logger named after the concrete module and class."""
        if not hasattr(self, "_logger"):
            class_name = self.__class__.__name__
            module_name = self.__class__.__module__
            self._logger = get_logger(f"{module_name}.{class_name}")
        return self._logger
