"""Brave Search API adapter (web + news results)."""

from __future__ import annotations

import json
import os
import time
from urllib.parse import urlencode, urlparse

from ..http import fetch
from .base import Article, SourceResult, clean_html

_last_call = 0.0
_MIN_INTERVAL = 1.1


def _api_key() -> str:
    return os.environ.get("BRAVE_SEARCH_API_KEY") or os.environ.get("BRAVE_API_KEY") or ""


def search(
    query: str,
    lang: str,
    settings: dict,
    *,
    country: str = "RU",
    freshness: str = "pd",
    count: int = 20,
) -> SourceResult:
    global _last_call
    source_name = "brave"
    key = _api_key()
    if not key:
        return SourceResult(source=source_name, articles=[], ok=False, error="BRAVE_SEARCH_API_KEY not set")

    wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()

    params = {
        "q": query,
        "count": count,
        "country": country,
        "search_lang": lang,
        "freshness": freshness,
        "result_filter": "web,news",
        "text_decorations": "false",
    }
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(params)
    started = time.time()
    result = fetch(
        url,
        settings,
        headers={"Accept": "application/json", "X-Subscription-Token": key},
    )
    if not result.ok:
        return SourceResult(
            source=source_name,
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
            source=source_name,
            articles=[],
            ok=False,
            http_status=result.status,
            error=f"json parse error: {exc}",
            duration_ms=int((time.time() - started) * 1000),
        )

    articles: list[Article] = []
    seen: set[str] = set()
    for bucket in ("news", "web"):
        for row in (data.get(bucket) or {}).get("results", []) or []:
            link = str(row.get("url", "")).strip()
            title = clean_html(str(row.get("title", ""))).strip()
            if not link or not title or link in seen:
                continue
            seen.add(link)
            profile = row.get("profile") or {}
            source = str(profile.get("name") or urlparse(link).netloc or "Brave")
            articles.append(
                Article(
                    title=title,
                    url=link,
                    source=source,
                    source_kind="web",
                    published_at=str(row.get("page_age") or row.get("age") or ""),
                    snippet=clean_html(str(row.get("description", "")))[:1000],
                    lang=lang,
                    query=query,
                )
            )
    return SourceResult(
        source=source_name,
        articles=articles,
        ok=True,
        http_status=result.status,
        duration_ms=int((time.time() - started) * 1000),
    )
