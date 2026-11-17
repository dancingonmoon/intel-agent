"""URL canonicalization, title fingerprints and event clustering."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from .sources.base import Article

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "yclid",
    "ref",
    "from",
    "source",
}
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)

STOPWORDS = {
    "и", "в", "во", "на", "для", "с", "со", "по", "из", "от", "за", "к", "ко", "о", "об",
    "что", "как", "это", "не", "но", "или", "а", "же", "ли", "бы", "при", "до", "после",
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with", "by", "from",
    "at", "is", "are", "was", "were", "be", "been", "this", "that", "its", "it",
}
# Longest first so that "ами" wins over "и".
_SUFFIXES = (
    "иями", "ями", "ами", "ов", "ев", "ий", "ый", "ой", "ей", "ая", "ое", "ые", "их",
    "ых", "ому", "ему", "ию", "ия", "ие", "ую", "юю", "а", "я", "ы", "и", "о", "е", "у",
    "ю", "ь",
)


def canonicalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if (parsed.hostname or "").endswith("bing.com") and parsed.path.endswith("/apiclick.aspx"):
        target = parse_qs(parsed.query).get("url", [""])[0]
        if target:
            url = target
            parsed = urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = parsed.path or "/"
    if path.endswith("/amp") or path.endswith("/amp/"):
        path = path[: -len("/amp")] or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    return urlunparse((scheme, netloc, path, "", urlencode(query), ""))


def title_fingerprint(title: str) -> str:
    title = (title or "").lower().replace("ё", "е")
    title = _PUNCT_RE.sub(" ", title)
    return _SPACE_RE.sub(" ", title).strip()


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) > len(suffix) + 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _tokens(article: Article) -> set[str]:
    text = (article.title + " " + article.snippet).lower().replace("ё", "е")
    return {_stem(word) for word in _WORD_RE.findall(text) if len(word) > 2 and word not in STOPWORDS}


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, title_fingerprint(left), title_fingerprint(right)).ratio()


def _published(article: Article) -> datetime | None:
    value = (article.published_at or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _near_in_time(left: Article, right: Article, days: int = 7) -> bool:
    a, b = _published(left), _published(right)
    if a is None or b is None:
        return True
    return abs(a - b) <= timedelta(days=days)


def _boost_hits(article: Article, boost_entities: list[str]) -> set[str]:
    text = (article.title + " " + article.snippet).lower()
    return {entity for entity in boost_entities if entity.lower() in text}


def should_cluster(left: Article, right: Article, boost_entities: list[str]) -> bool:
    if left.lang and right.lang and left.lang != right.lang:
        return False
    if similarity(left.title, right.title) >= 0.88:
        return True
    if not _near_in_time(left, right):
        return False
    shared_tokens = _tokens(left) & _tokens(right)
    shared_boost = _boost_hits(left, boost_entities) & _boost_hits(right, boost_entities)
    # Same named entity + two shared event words is enough for paraphrases.
    if shared_boost and len(shared_tokens) >= 2:
        return True
    # Otherwise require a stronger lexical overlap.
    union = _tokens(left) | _tokens(right)
    return len(shared_tokens) >= 4 and len(shared_tokens) / max(len(union), 1) >= 0.25


def group_articles(
    articles: list[Article], boost_entities: list[str] | None = None, threshold: float = 0.90
) -> list[list[Article]]:
    boost_entities = boost_entities or []
    groups: list[list[Article]] = []
    by_url: dict[str, int] = {}
    for article in articles:
        key = canonicalize_url(article.url)
        if key in by_url:
            groups[by_url[key]].append(article)
            continue
        placed = False
        for idx, group in enumerate(groups):
            if any(should_cluster(article, member, boost_entities) for member in group):
                group.append(article)
                by_url[key] = idx
                placed = True
                break
        if not placed:
            by_url[key] = len(groups)
            groups.append([article])
    return groups


def best_member(group: list[Article]) -> Article:
    """Prefer official, then media, then the earliest member."""
    rank = {"official": 0, "media": 1, "aggregator": 2, "web": 3, "telegram": 4, "social": 5}
    return sorted(group, key=lambda a: (rank.get(a.source_kind, 9), a.published_at or "9999"))[0]
