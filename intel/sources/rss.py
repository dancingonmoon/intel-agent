"""RSS adapters: Google News RSS and generic media feeds."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urlparse

from ..http import fetch
from .base import Article, SourceResult, clean_html, parse_rfc822, strip_publisher

LOCALES = {
    "ru": {"hl": "ru", "gl": "RU", "ceid": "RU:ru"},
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node:
        if _local(child.tag) in names and child.text:
            return child.text
    return ""


def _parse_xml(text: str):
    """Strict parse first; fall back to lxml recover mode for broken feeds."""
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        try:
            from lxml import etree
        except ImportError:
            raise exc
        root = etree.fromstring(
            text.encode("utf-8", errors="replace"),
            etree.XMLParser(recover=True, huge_tree=True),
        )
        if root is None:
            raise exc
        return root


def _parse_feed(text: str, default_source: str, lang: str = "") -> list[Article]:
    root = _parse_xml(text)
    articles: list[Article] = []

    # RSS 2.0
    channel = root.find("channel")
    if channel is None and _local(root.tag) == "rss":
        channel = root
    if channel is not None:
        feed_title = _child_text(channel, ("title",)) or default_source
        for item in channel.findall("item"):
            title = clean_html(_child_text(item, ("title",)))
            link = _child_text(item, ("link",))
            source = _child_text(item, ("source",)) or feed_title or default_source
            description = clean_html(_child_text(item, ("description", "encoded", "summary")))
            published = parse_rfc822(_child_text(item, ("pubdate", "date", "updated")))
            if not title or not link:
                continue
            articles.append(
                Article(
                    title=strip_publisher(title, source),
                    url=link.strip(),
                    source=source or default_source,
                    source_kind="media",
                    published_at=published,
                    snippet=description[:1000],
                    lang=lang,
                )
            )
        return articles

    # Atom
    if _local(root.tag) == "feed":
        feed_title = _child_text(root, ("title",)) or default_source
        for entry in root:
            if _local(entry.tag) != "entry":
                continue
            title = clean_html(_child_text(entry, ("title",)))
            link = ""
            for child in entry:
                if _local(child.tag) == "link":
                    link = child.attrib.get("href", "")
                    if child.attrib.get("rel", "alternate") == "alternate":
                        break
            summary = clean_html(_child_text(entry, ("summary", "content")))
            published = parse_rfc822(_child_text(entry, ("published", "updated")))
            if not title or not link:
                continue
            articles.append(
                Article(
                    title=title,
                    url=link.strip(),
                    source=feed_title or default_source,
                    source_kind="media",
                    published_at=published,
                    snippet=summary[:1000],
                    lang=lang,
                )
            )
    return articles


def fetch_google_news(query: str, lang: str, settings: dict, window_hours: int = 24) -> SourceResult:
    locale = LOCALES.get(lang, LOCALES["en"])
    if window_hours <= 48:
        when = "1d"
    elif window_hours <= 168:
        when = "7d"
    elif window_hours <= 720:
        when = "30d"
    else:
        when = None  # backfill: fetch all, filter by published_at locally
    q = query if when is None else f"{query} when:{when}"
    url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(q)
        + f"&hl={locale['hl']}&gl={locale['gl']}&ceid={locale['ceid']}"
    )
    started = time.time()
    result = fetch(url, settings)
    source_name = f"google_news_{lang}"
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
        articles = _parse_feed(result.text, default_source="Google News", lang=lang)
    except ET.ParseError as exc:
        return SourceResult(
            source=source_name,
            articles=[],
            ok=False,
            http_status=result.status,
            error=f"xml parse error: {exc}",
            duration_ms=int((time.time() - started) * 1000),
        )
    return SourceResult(
        source=source_name,
        articles=articles,
        ok=True,
        http_status=result.status,
        duration_ms=int((time.time() - started) * 1000),
    )


def fetch_bing_news(query: str, lang: str, settings: dict) -> SourceResult:
    """Bing News RSS is free and needs no key; recall is smaller than Google."""
    locale = {"ru": ("ru", "RU"), "en": ("en-US", "US")}.get(lang, ("en-US", "US"))
    url = (
        "https://www.bing.com/news/search?q="
        + quote_plus(query)
        + f"&format=rss&setlang={locale[0]}&cc={locale[1]}"
    )
    started = time.time()
    result = fetch(url, settings)
    name = "bing_news"
    if not result.ok:
        return SourceResult(
            source=name,
            articles=[],
            ok=False,
            http_status=result.status or None,
            error=result.error or "fetch failed",
            duration_ms=result.duration_ms,
        )
    try:
        articles = _parse_feed(result.text, default_source="Bing News", lang=lang)
    except ET.ParseError as exc:
        return SourceResult(
            source=name,
            articles=[],
            ok=False,
            http_status=result.status,
            error=f"xml parse error: {exc}",
            duration_ms=int((time.time() - started) * 1000),
        )
    return SourceResult(
        source=name,
        articles=articles,
        ok=True,
        http_status=result.status,
        duration_ms=int((time.time() - started) * 1000),
    )


def fetch_generic_rss(url: str, settings: dict, lang: str = "", source_name: str = "") -> SourceResult:
    started = time.time()
    result = fetch(url, settings)
    name = source_name or (urlparse(url).netloc or url)
    if not result.ok:
        return SourceResult(
            source=name,
            articles=[],
            ok=False,
            http_status=result.status or None,
            error=result.error or "fetch failed",
            duration_ms=result.duration_ms,
        )
    try:
        articles = _parse_feed(result.text, default_source=name, lang=lang)
    except ET.ParseError as exc:
        return SourceResult(
            source=name,
            articles=[],
            ok=False,
            http_status=result.status,
            error=f"xml parse error: {exc}",
            duration_ms=int((time.time() - started) * 1000),
        )
    return SourceResult(
        source=name,
        articles=articles,
        ok=True,
        http_status=result.status,
        duration_ms=int((time.time() - started) * 1000),
    )
