"""HTTP routes of the admin panel."""

ERROR_LEVELS = frozenset({"ERROR", "CRITICAL"})
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def normalize_level(value: str | None, allowed: frozenset[str]) -> str | None:
    """Normalize an optional level query parameter.

    Args:
        value: Raw query value.
        allowed: Levels accepted by the page.

    Returns:
        Uppercase level or None for "all"/invalid values.
    """
    candidate = (value or "").strip().upper()
    return candidate if candidate in allowed else None
