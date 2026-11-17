"""Telegram public-channel preview adapter (t.me/s/<channel>).

No account is needed. This only reads channels whose public preview is
enabled; it cannot search Telegram globally.
"""

from __future__ import annotations

import time

from bs4 import BeautifulSoup

from ..http import fetch
from .base import Article, SourceResult, clean_html


def fetch_channel(channel: str, settings: dict, lang: str = "ru") -> SourceResult:
    channel = channel.lstrip("@").strip()
    url = f"https://t.me/s/{channel}"
    started = time.time()
    result = fetch(url, settings)
    source_name = f"telegram:@{channel}"
    if not result.ok:
        return SourceResult(
            source=source_name,
            articles=[],
            ok=False,
            http_status=result.status or None,
            error=result.error or "fetch failed",
            duration_ms=result.duration_ms,
        )

    soup = BeautifulSoup(result.text, "lxml")
    nodes = soup.select("div.tgme_widget_message")
    if not nodes:
        return SourceResult(
            source=source_name,
            articles=[],
            ok=False,
            http_status=result.status,
            error="no public preview or no messages",
            duration_ms=int((time.time() - started) * 1000),
        )

    articles: list[Article] = []
    for node in nodes:
        post = node.get("data-post", "")
        text_node = node.select_one("div.tgme_widget_message_text")
        text = clean_html(text_node.decode_contents()) if text_node else ""
        if not text:
            continue
        time_node = node.select_one("time[datetime]")
        published = time_node.get("datetime", "") if time_node else ""
        link_node = node.select_one("a.tgme_widget_message_date")
        link = link_node.get("href", "") if link_node else (f"https://t.me/{post}" if post else "")
        if not link:
            continue
        first_line = text.split("\n", 1)[0].strip()
        title = first_line if len(first_line) <= 140 else first_line[:137].rstrip() + "…"
        articles.append(
            Article(
                title=title,
                url=link,
                source=source_name,
                source_kind="telegram",
                published_at=published,
                snippet=text[:1000],
                lang=lang,
            )
        )

    return SourceResult(
        source=source_name,
        articles=articles,
        ok=True,
        http_status=result.status,
        duration_ms=int((time.time() - started) * 1000),
    )
