"""Relevance filtering and rule-based facet classification."""

from __future__ import annotations

import re

from .contracts import FACETS
from .sources.base import Article

_SPACE_RE = re.compile(r"\s+")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def normalize_text(value: str) -> str:
    value = (value or "").lower().replace("ё", "е")
    return _SPACE_RE.sub(" ", value).strip()


def contains_term(text: str, term: str) -> bool:
    # Keep intentional leading/trailing spaces: "PoC " must not match "Pocket".
    term = (term or "").lower().replace("ё", "е")
    term = _SPACE_RE.sub(" ", term)
    return bool(term) and term in text


def is_excluded(article: Article, topic: dict) -> bool:
    text = normalize_text(article.title + " " + article.snippet)
    return any(contains_term(text, term) for term in topic.get("exclude", []))


def is_source_excluded(article: Article, topic: dict) -> bool:
    haystack = (article.url + " " + article.source).lower()
    return any(str(domain).lower() in haystack for domain in topic.get("exclude_sources", []))


def is_relevant(article: Article, topic: dict) -> bool:
    if is_source_excluded(article, topic):
        return False
    text = normalize_text(article.title + " " + article.snippet)
    entities = topic.get("entities", {})
    required = entities.get("required_any", [])
    query_text = normalize_text(article.query)
    # Only Yandex-style news aggregators get query-provenance leniency. Web
    # search results (Brave/Bing) still need the topic term in the title,
    # otherwise "oil terminal"/"radio station" pages slip through.
    query_has_topic = (
        bool(required)
        and article.source_kind == "aggregator"
        and any(contains_term(query_text, term) for term in required)
    )

    if required and not query_has_topic and not any(contains_term(text, term) for term in required):
        return False

    if query_has_topic:
        # Query-provenance results may omit "TETRA" from the headline/snippet.
        # Require some radio/communications context so unrelated company news
        # fetched by a broad query does not flood the report.
        context = entities.get("context_any") or entities.get("social_context_any") or []
        if context and not any(contains_term(text, term) for term in context):
            return False

    title_required = entities.get("require_in_title", [])
    source_text = f"{article.source} {article.url}".lower()
    source_hit = any(str(term).lower() in source_text for term in entities.get("source_any", []))
    if title_required and not query_has_topic and not source_hit:
        title = normalize_text(article.title)
        if not any(contains_term(title, term) for term in title_required):
            return False
    geo = entities.get("geo_any", [])
    if geo:
        boosted = entities.get("boost", [])
        geo_hit = any(contains_term(text, term) for term in geo)
        boost_hit = any(contains_term(text, term) for term in boosted)
        if not geo_hit and not boost_hit and not source_hit:
            return False
    url = (article.url or "").lower()
    is_social = (
        article.source_kind == "social"
        or ("x.com/" in url and "/status/" in url)
        or ("twitter.com/" in url and "/status/" in url)
    )
    if is_social:
        # A username/display name containing "Tetra" is not a TETRA story.
        # Require an actual radio/communications term in the post body.
        social_context = entities.get(
            "social_context_any",
            ["радио", "радиосвяз", "связ", "терминал", "базов", "станц", "pmr", "профессиональн", "radio", "communication", "network", "terminal"],
        )
        post = normalize_text(article.snippet or article.title)
        if social_context and not any(contains_term(post, term) for term in social_context):
            return False
    return not is_excluded(article, topic)


def detect_lang(text: str) -> str:
    sample = (text or "")[:500]
    if not sample:
        return ""
    cyrillic = len(_CYRILLIC_RE.findall(sample))
    return "ru" if cyrillic / max(len(sample), 1) > 0.15 else "en"


def classify(article: Article, topic: dict) -> tuple[str, list[str], str, list[str]]:
    """Return (primary, secondary, confidence, matched_terms)."""
    text = normalize_text(article.title + " " + article.snippet)
    scores: dict[str, int] = {facet: 0 for facet in FACETS}
    matched: dict[str, list[str]] = {facet: [] for facet in FACETS}

    for facet, rules in (topic.get("facets") or {}).items():
        if facet not in FACETS:
            continue
        for term in rules.get("strong", []) or []:
            if contains_term(text, str(term)):
                scores[facet] += 3
                matched[facet].append(str(term))
        for term in rules.get("weak", []) or []:
            if contains_term(text, str(term)):
                scores[facet] += 1
                matched[facet].append(str(term))

    best = max(scores.values()) if scores else 0
    primary = "other"
    if best >= 3:
        for facet in FACETS:
            if scores[facet] == best:
                primary = facet
                break

    secondary = [
        facet
        for facet in FACETS
        if facet != primary and scores[facet] >= 2
    ]
    secondary.sort(key=lambda f: scores[f], reverse=True)

    matched_terms: list[str] = []
    for facet in ([primary] if primary in FACETS else []) + secondary:
        for term in matched.get(facet, []):
            if term not in matched_terms:
                matched_terms.append(term)

    if article.source_kind == "official" or best >= 6:
        confidence = "high"
    elif best >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    return primary, secondary, confidence, matched_terms
