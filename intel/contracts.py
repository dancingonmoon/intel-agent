"""Versioned artifact contracts.

`digest.json` is the single source of truth: it carries every URL, original
title, source and date. `report.json` may only reference items by id and add
Chinese prose. The renderer resolves all citations from the digest, so an LLM
cannot invent a link.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1
FACETS = ("contracts", "industry", "technology", "players")
FACET_LABELS = {
    "contracts": "重大合同",
    "industry": "行业动态",
    "technology": "技术发展",
    "players": "主要玩家",
}
CONFIDENCE = ("high", "medium", "low")
SOURCE_KINDS = ("aggregator", "media", "official", "social", "telegram", "web")

# Fields the LLM must never emit in report.json.
FORBIDDEN_REPORT_ITEM_KEYS = {
    "url",
    "canonical_url",
    "title_original",
    "source",
    "source_kind",
    "published_at",
    "date",
    "lang",
    "entities",
    "confidence",
    "corroboration_count",
}


def _as_dict(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be an object")
    return value


def _as_list(value: Any, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{what} must be an array")
    return value


@dataclass(slots=True)
class Window:
    start: str = ""
    end: str = ""
    tz: str = "Europe/Moscow"
    kind: str = "rolling"

    @classmethod
    def from_dict(cls, data: dict | None) -> "Window":
        data = data or {}
        return cls(
            start=str(data.get("start", "")),
            end=str(data.get("end", "")),
            tz=str(data.get("tz", "Europe/Moscow")),
            kind=str(data.get("kind", "rolling")),
        )


@dataclass(slots=True)
class Facets:
    primary: str = "other"
    secondary: list[str] = field(default_factory=list)
    confidence: str = "medium"
    matched_terms: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Facets":
        data = data or {}
        return cls(
            primary=str(data.get("primary", "other")),
            secondary=[str(x) for x in _as_list(data.get("secondary"), "facets.secondary")],
            confidence=str(data.get("confidence", "medium")),
            matched_terms=[str(x) for x in _as_list(data.get("matched_terms"), "facets.matched_terms")],
        )


@dataclass(slots=True)
class Item:
    item_id: str
    cluster_id: str
    canonical_url: str
    title_original: str
    source: str
    source_kind: str
    published_at: str
    first_seen: str
    snippet: str = ""
    lang: str = ""
    url_kind: str = "direct"
    facets: Facets = field(default_factory=Facets)
    entities: list[str] = field(default_factory=list)
    corroboration_count: int = 1
    matched_queries: list[str] = field(default_factory=list)
    found_by: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Item":
        data = _as_dict(data, "item")
        for key in ("item_id", "cluster_id", "canonical_url", "title_original", "source", "published_at"):
            if not data.get(key):
                raise ValueError(f"item missing required field: {key}")
        return cls(
            item_id=str(data["item_id"]),
            cluster_id=str(data["cluster_id"]),
            canonical_url=str(data["canonical_url"]),
            title_original=str(data["title_original"]),
            source=str(data["source"]),
            source_kind=str(data.get("source_kind", "media")),
            published_at=str(data["published_at"]),
            first_seen=str(data.get("first_seen", data["published_at"])),
            snippet=str(data.get("snippet", "")),
            lang=str(data.get("lang", "")),
            url_kind=str(data.get("url_kind", "direct")),
            facets=Facets.from_dict(data.get("facets")),
            entities=[str(x) for x in _as_list(data.get("entities"), "entities")],
            corroboration_count=int(data.get("corroboration_count", 1)),
            matched_queries=[str(x) for x in _as_list(data.get("matched_queries"), "matched_queries")],
            found_by=[str(x) for x in _as_list(data.get("found_by"), "found_by")],
        )


@dataclass(slots=True)
class Cluster:
    cluster_id: str
    story_key: str = ""
    member_count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    update_hash: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Cluster":
        data = _as_dict(data, "cluster")
        if not data.get("cluster_id"):
            raise ValueError("cluster missing cluster_id")
        return cls(
            cluster_id=str(data["cluster_id"]),
            story_key=str(data.get("story_key", "")),
            member_count=int(data.get("member_count", 1)),
            first_seen=str(data.get("first_seen", "")),
            last_seen=str(data.get("last_seen", "")),
            update_hash=str(data.get("update_hash", "")),
        )


@dataclass(slots=True)
class SourceHealth:
    source: str
    ok: bool = True
    count: int = 0
    http_status: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    degraded_reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "SourceHealth":
        data = _as_dict(data, "source_health")
        if not data.get("source"):
            raise ValueError("source_health missing source")
        status = data.get("http_status")
        return cls(
            source=str(data["source"]),
            ok=bool(data.get("ok", True)),
            count=int(data.get("count", 0)),
            http_status=int(status) if status is not None else None,
            duration_ms=int(data["duration_ms"]) if data.get("duration_ms") is not None else None,
            error=str(data["error"]) if data.get("error") else None,
            degraded_reason=str(data["degraded_reason"]) if data.get("degraded_reason") else None,
        )


@dataclass(slots=True)
class Digest:
    schema_version: int
    run_id: str
    topic: str
    window: Window
    items: list[Item]
    clusters: list[Cluster] = field(default_factory=list)
    source_health: list[SourceHealth] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Digest":
        data = _as_dict(data, "digest")
        if int(data.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ValueError(f"unsupported digest schema_version: {data.get('schema_version')}")
        return cls(
            schema_version=SCHEMA_VERSION,
            run_id=str(data.get("run_id", "")),
            topic=str(data.get("topic", "")),
            window=Window.from_dict(data.get("window")),
            items=[Item.from_dict(x) for x in _as_list(data.get("items"), "items")],
            clusters=[Cluster.from_dict(x) for x in _as_list(data.get("clusters"), "clusters")],
            source_health=[SourceHealth.from_dict(x) for x in _as_list(data.get("source_health"), "source_health")],
        )

    @property
    def items_by_id(self) -> dict[str, Item]:
        return {item.item_id: item for item in self.items}

    @property
    def clusters_by_id(self) -> dict[str, Cluster]:
        return {cluster.cluster_id: cluster for cluster in self.clusters}


@dataclass(slots=True)
class ReportItem:
    item_id: str
    title_zh: str
    summary_zh: str
    why_it_matters: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ReportItem":
        data = _as_dict(data, "report item")
        return cls(
            item_id=str(data.get("item_id", "")),
            title_zh=str(data.get("title_zh", "")),
            summary_zh=str(data.get("summary_zh", "")),
            why_it_matters=str(data.get("why_it_matters", "")),
        )


@dataclass(slots=True)
class Coverage:
    ok: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    no_news_facets: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Coverage":
        data = data or {}
        return cls(
            ok=[str(x) for x in _as_list(data.get("ok"), "coverage.ok")],
            degraded=[str(x) for x in _as_list(data.get("degraded"), "coverage.degraded")],
            failed=[str(x) for x in _as_list(data.get("failed"), "coverage.failed")],
            no_news_facets=[str(x) for x in _as_list(data.get("no_news_facets"), "coverage.no_news_facets")],
        )


@dataclass(slots=True)
class Report:
    schema_version: int
    run_id: str
    report_date: str
    headline_item_ids: list[str]
    sections: dict[str, list[ReportItem]]
    title_en: dict[str, str] = field(default_factory=dict)
    watchlist: list[dict] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    week_review: list[str] = field(default_factory=list)
    degraded: str | None = None

    @classmethod
    def from_dict(cls, data: dict, degraded: str | None = None) -> "Report":
        data = _as_dict(data, "report")
        if int(data.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ValueError(f"unsupported report schema_version: {data.get('schema_version')}")
        raw_sections = _as_dict(data.get("sections", {}), "sections")
        sections: dict[str, list[ReportItem]] = {}
        for facet in FACETS:
            sections[facet] = [ReportItem.from_dict(x) for x in _as_list(raw_sections.get(facet), f"sections.{facet}")]
        raw_title_en = _as_dict(data.get("title_en") or {}, "title_en")
        title_en = {str(key): str(value).strip() for key, value in raw_title_en.items() if str(value).strip()}
        return cls(
            schema_version=SCHEMA_VERSION,
            run_id=str(data.get("run_id", "")),
            report_date=str(data.get("report_date", "")),
            headline_item_ids=[str(x) for x in _as_list(data.get("headline_item_ids"), "headline_item_ids")],
            sections=sections,
            title_en=title_en,
            watchlist=[x for x in _as_list(data.get("watchlist"), "watchlist") if isinstance(x, dict)],
            coverage=Coverage.from_dict(data.get("coverage")),
            week_review=[str(x) for x in _as_list(data.get("week_review"), "week_review")],
            degraded=degraded if degraded is not None else data.get("degraded"),
        )


def validate_report_data(report_data: dict, digest: Digest) -> list[str]:
    """Return a list of human-readable contract violations (empty = valid)."""
    errors: list[str] = []
    if not isinstance(report_data, dict):
        return ["report must be a JSON object"]
    if int(report_data.get("schema_version", 0)) != SCHEMA_VERSION:
        errors.append(f"report.schema_version must be {SCHEMA_VERSION}")

    item_ids = {item.item_id for item in digest.items}
    cluster_ids = {cluster.cluster_id for cluster in digest.clusters}

    headlines = report_data.get("headline_item_ids") or []
    if not isinstance(headlines, list):
        errors.append("headline_item_ids must be an array")
    else:
        for iid in headlines:
            if iid not in item_ids:
                errors.append(f"headline references unknown item_id: {iid}")

    title_en = report_data.get("title_en") or {}
    if not isinstance(title_en, dict):
        errors.append("title_en must be an object")
    else:
        for iid, translated in title_en.items():
            if iid not in item_ids:
                errors.append(f"title_en references unknown item_id: {iid}")
            if not isinstance(translated, str) or not translated.strip():
                errors.append(f"title_en[{iid}] must be a non-empty string")

    sections = report_data.get("sections")
    if not isinstance(sections, dict):
        errors.append("sections must be an object")
        sections = {}
    for facet, entries in sections.items():
        if facet not in FACETS:
            errors.append(f"unknown section facet: {facet}")
            continue
        if not isinstance(entries, list):
            errors.append(f"sections.{facet} must be an array")
            continue
        for idx, entry in enumerate(entries):
            where = f"sections.{facet}[{idx}]"
            if not isinstance(entry, dict):
                errors.append(f"{where} must be an object")
                continue
            forbidden = FORBIDDEN_REPORT_ITEM_KEYS & set(entry)
            if forbidden:
                errors.append(f"{where} contains forbidden fields: {sorted(forbidden)}")
            iid = entry.get("item_id")
            if iid not in item_ids:
                errors.append(f"{where} references unknown item_id: {iid}")
            if not str(entry.get("title_zh", "")).strip():
                errors.append(f"{where}.title_zh is empty")
            if not str(entry.get("summary_zh", "")).strip():
                errors.append(f"{where}.summary_zh is empty")

    week_review = report_data.get("week_review")
    if week_review is not None:
        if not isinstance(week_review, list):
            errors.append("week_review must be an array")
        else:
            for idx, entry in enumerate(week_review):
                if not isinstance(entry, str) or not entry.strip():
                    errors.append(f"week_review[{idx}] must be a non-empty string")
                elif len(entry) > 300:
                    errors.append(f"week_review[{idx}] is too long")

    watchlist = report_data.get("watchlist") or []
    if not isinstance(watchlist, list):
        errors.append("watchlist must be an array")
    else:
        for idx, entry in enumerate(watchlist):
            if not isinstance(entry, dict):
                errors.append(f"watchlist[{idx}] must be an object")
                continue
            cid = entry.get("cluster_id")
            if cid and cid not in cluster_ids:
                errors.append(f"watchlist[{idx}] references unknown cluster_id: {cid}")

    coverage = report_data.get("coverage") or {}
    if not isinstance(coverage, dict):
        errors.append("coverage must be an object")
    else:
        for facet in coverage.get("no_news_facets") or []:
            if facet not in FACETS:
                errors.append(f"coverage.no_news_facets has unknown facet: {facet}")

    # A section listed as "no news" must be empty.
    no_news = set((coverage or {}).get("no_news_facets") or []) if isinstance(coverage, dict) else set()
    for facet in no_news:
        if sections.get(facet):
            errors.append(f"coverage says no news for {facet} but section is non-empty")

    return errors
