"""Shared item model returned by every collector."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    """One collected piece of content from any channel."""

    channel: str  # "x" | "reddit" | "web" | "news" | "linkedin"
    title: str
    url: str
    summary: str = ""
    source_label: str = ""  # e.g. "r/LLMDevs", "@handle", "reuters.com"
    published: datetime | None = None
    engagement: int = 0  # reddit score when the Data API is available; 0 via RSS
    niche_hint: str = ""  # niche id suggested by the source (collector or model)
    extra: dict = field(default_factory=dict)


_TAG_RE = None


def strip_html(text: str) -> str:
    """Collapse an HTML fragment to plain text (Atom summaries arrive as HTML)."""
    import re

    global _TAG_RE
    if _TAG_RE is None:
        _TAG_RE = re.compile(r"<[^>]+>")
    return _TAG_RE.sub(" ", text or "").strip()
