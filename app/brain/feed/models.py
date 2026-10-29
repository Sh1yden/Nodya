"""Shared models of the feed subsystem."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeedItem(BaseModel):
    """One normalized entry of a feed."""

    entry_id: str
    title: str
    summary: str = ""
    link: str = ""


class RelevanceScore(BaseModel):
    """Validated relevance verdict of the BP-role model."""

    score: float = Field(ge=0.0, le=10.0)
    summary: str = Field(min_length=1)


class ResolvedSource(BaseModel):
    """Canonical source produced by the URL resolver."""

    url: str
    kind: Literal["rss", "telegram"]
    title: str | None = None
