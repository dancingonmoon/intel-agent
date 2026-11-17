"""GDELT DOC 2.0 adapter.

Free, no key, but rate-limited to one request every five seconds and returns
metadata only (no article body). Used as a multilingual discovery source.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from ..http import fetch
from .base import Article, SourceResult

_last_call = 0.0
_MIN_INTERVAL = 6.0


def _iso(seendate: str) -> str:
    try:
        return datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return str(seendate or "")


def search(query: str, lang: str, settings: dict, window_hours: int = 24) -> SourceResult:
    global _last_call
    if window_hours <= 48:
        timespan = "1d"
    elif window_hours <= 168:
        timespan = "1w"
    elif window_hours <= 720:
        timespan = "1m"
    else:
        timespan = "3m"

    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()

    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": 75,
        "format": "json",
        "sort": "datedesc",
        "timespan": timespan,
    }
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(params)
    started = time.time()
    result = fetch(url, settings, timeout=30)
    if not result.ok:
        return SourceResult(
            source="gdelt",
            articles=[],
            ok=False,
            http_status=result.status or None,
            error=result.error or "fetch failed",
            duration_ms=result.duration_ms,
        )
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError as exc:
        return SourceResult(
            source="gdelt",
            articles=[],
            ok=False,
            http_status=result.status,
            error=f"json parse error: {exc}",
            duration_ms=int((time.time() - started) * 1000),
        )

    articles: list[Article] = []
    for row in data.get("articles", []) or []:
        link = str(row.get("url", "")).strip()
        title = str(row.get("title", "")).strip()
        if not link or not title:
            continue
        articles.append(
            Article(
                title=title,
                url=link,
                source=urlparse(link).netloc or str(row.get("domain", "GDELT")),
                source_kind="media",
                published_at=_iso(str(row.get("seendate", ""))),
                snippet="",
                lang=str(row.get("language", "")) or lang,
                query=query,
            )
        )
    return SourceResult(
        source="gdelt",
        articles=articles,
        ok=True,
        http_status=result.status,
        duration_ms=int((time.time() - started) * 1000),
    )
