"""Text helpers shared by feed parsing and scoring."""

from __future__ import annotations

import html
import json
import re
from typing import Any

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(raw: str) -> str:
    """Strip HTML tags and collapse whitespace of feed text.

    Args:
        raw: Raw entry field (may contain markup entities).

    Returns:
        Plain single-line text.
    """
    stripped = _HTML_TAG_RE.sub(" ", raw)
    return " ".join(html.unescape(stripped).split())


def parse_json(raw: str) -> dict[str, Any]:
    """Parse a model answer into a JSON object defensively.

    Args:
        raw: Raw model text (fences allowed).

    Returns:
        Parsed JSON mapping.

    Raises:
        ValueError: No valid JSON object found.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output.")
    return json.loads(cleaned[start : end + 1])
