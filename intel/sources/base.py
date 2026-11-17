"""Shared source types and text helpers."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class Article:
    title: str
    url: str
    source: str
    source_kind: str
    published_at: str
    snippet: str = ""
    lang: str = ""
    query: str = ""
    facet_hint: str = ""


@dataclass(slots=True)
class SourceResult:
    source: str
    articles: list[Article]
    ok: bool = True
    http_status: int | None = None
    error: str | None = None
    duration_ms: int = 0


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_rfc822(value: str) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value.strip()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def strip_publisher(title: str, source: str) -> str:
    """Google News appends ' - Publisher'; remove it when it matches."""
    title = (title or "").strip()
    if not title:
        return title
    for separator in (" - ", " — ", " | "):
        if separator in title:
            head, tail = title.rsplit(separator, 1)
            if source and tail.strip().lower() == source.strip().lower():
                return head.strip()
    return title
