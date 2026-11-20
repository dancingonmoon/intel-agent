"""Collection pipeline: adapters -> relevance -> clustering -> digest."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import Cluster, Digest, Facets, Item, SourceHealth, Window
from .dedupe import best_member, canonicalize_url, group_articles
from .relevance import classify, contains_term, detect_lang, is_relevant
from .sources import brave as brave_source
from .sources import rss, telegram
from .sources.base import Article, SourceResult
from .store import Store

DEFAULT_WINDOW_HOURS = 24


class PipelineNotReady(NotImplementedError):
    pass


def _run_id(topic_id: str) -> str:
    return datetime.now().strftime("%Y%m%dT%H%M") + f"-{topic_id}"


def _aggregate_health(results: list[SourceResult]) -> list[SourceHealth]:
    grouped: dict[str, SourceHealth] = {}
    for result in results:
        health = grouped.get(result.source)
        if health is None:
            grouped[result.source] = SourceHealth(
                source=result.source,
                ok=result.ok,
                count=len(result.articles),
                http_status=result.http_status,
                duration_ms=result.duration_ms,
                error=result.error,
            )
        else:
            health.count += len(result.articles)
            health.ok = health.ok or result.ok
            health.duration_ms = (health.duration_ms or 0) + (result.duration_ms or 0)
            if result.error and not health.error:
                health.error = result.error
    return list(grouped.values())


def _count_tasks(topic: dict) -> int:
    queries = topic.get("queries", {})
    disabled = {str(x) for x in topic.get("disabled_sources", [])}
    engines = topic.get("engines", {})
    priority = engines.get("priority") or []
    total = 0
    total += sum(len(v or []) for v in (queries.get("google_news") or {}).values())
    total += sum(len(v or []) for v in (queries.get("bing_news") or {}).values())
    if "gdelt" in priority and "gdelt" not in disabled:
        total += len(queries.get("gdelt") or [])
    total += sum(1 for url in queries.get("rss") or [] if str(url) not in disabled)
    total += sum(
        1 for channel in queries.get("telegram_channels") or [] if f"telegram:@{channel}" not in disabled
    )
    if "x" in priority and "x" not in disabled:
        total += len(queries.get("x") or [])
    yandex_mode = str(engines.get("yandex_mode", "off")).lower()
    if "yandex" in priority and "yandex" not in disabled and yandex_mode not in ("off", "false", "0", "none", ""):
        total += sum(len(v or []) for v in (queries.get("yandex") or {}).values())
    if "brave" in priority and "brave" not in disabled:
        total += sum(len(v or []) for v in (queries.get("brave") or {}).values())
    return total


def _collect_results(topic: dict, settings: dict, window_hours: int, progress=None) -> list[SourceResult]:
    results: list[SourceResult] = []
    queries = topic.get("queries", {})
    disabled = {str(x) for x in topic.get("disabled_sources", [])}
    total = _count_tasks(topic)
    done = 0

    def tick(label: str) -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress.step(done, total, label[:52])

    for lang, query_list in (queries.get("google_news") or {}).items():
        for query in query_list or []:
            results.append(rss.fetch_google_news(str(query), str(lang), settings, window_hours))
            tick(f"Google News {lang}: {query}")
            time.sleep(0.8)

    for lang, query_list in (queries.get("bing_news") or {}).items():
        for query in query_list or []:
            results.append(rss.fetch_bing_news(str(query), str(lang), settings))
            tick(f"Bing News {lang}: {query}")
            time.sleep(0.4)

    if "gdelt" in (topic.get("engines", {}).get("priority") or []) and "gdelt" not in disabled:
        from .sources import gdelt as gdelt_source

        for query in queries.get("gdelt") or []:
            results.append(gdelt_source.search(str(query), "ru", settings, window_hours))
            tick(f"GDELT: {query}")

    for url in queries.get("rss") or []:
        if str(url) in disabled:
            continue
        results.append(rss.fetch_generic_rss(str(url), settings, lang=str(topic.get("languages", ["ru"])[0])))
        tick(f"RSS: {url}")
        time.sleep(0.4)

    for channel in queries.get("telegram_channels") or []:
        if f"telegram:@{channel}" in disabled:
            continue
        results.append(telegram.fetch_channel(str(channel), settings))
        tick(f"Telegram: @{channel}")
        time.sleep(0.6)

    engines = topic.get("engines", {})
    priority = engines.get("priority") or []

    if "x" in priority and "x" not in disabled and queries.get("x"):
        from .sources import x as x_source

        for query in queries.get("x") or []:
            results.append(x_source.search(str(query), settings, window_hours))
            tick(f"X: {query}")

    yandex_mode = str(engines.get("yandex_mode", "off")).lower()
    yandex_enabled = yandex_mode not in ("off", "false", "0", "none", "")
    if "yandex" in priority and "yandex" not in disabled and yandex_enabled and queries.get("yandex"):
        from .sources import yandex as yandex_source

        for lang, query_list in (queries.get("yandex") or {}).items():
            for query in query_list or []:
                results.append(yandex_source.search(str(query), str(lang), settings))
                tick(f"Yandex {lang}: {query}")

    if "brave" in priority and "brave" not in disabled:
        if window_hours <= 48:
            freshness = "pd"
        elif window_hours <= 168:
            freshness = "pw"
        elif window_hours <= 720:
            freshness = "pm"
        else:
            freshness = "py"
        for lang, query_list in (queries.get("brave") or {}).items():
            country = "RU" if str(lang) == "ru" else "US"
            for query in query_list or []:
                results.append(
                    brave_source.search(str(query), str(lang), settings, country=country, freshness=freshness)
                )
                tick(f"Brave {lang}: {query}")
    return results


def _is_seen(article: Article, seen: dict[str, str]) -> bool:
    return canonicalize_url(article.url) in seen


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _within_window(article: Article, window: Window) -> bool:
    published = _parse_dt(article.published_at)
    start = _parse_dt(window.start)
    end = _parse_dt(window.end)
    if published is None or start is None or end is None:
        return True
    # Allow a one-day clock skew for feeds that only publish dates.
    return start <= published <= end + timedelta(days=1)


def _build_digest(
    topic: dict,
    run_id: str,
    window: Window,
    results: list[SourceResult],
    seen: dict[str, str],
    include_seen: bool = False,
) -> Digest:
    articles: list[Article] = []
    origins: dict[str, set[str]] = {}
    for result in results:
        for article in result.articles:
            if not article.lang:
                article.lang = detect_lang(article.title + " " + article.snippet)
            articles.append(article)
            origins.setdefault(canonicalize_url(article.url), set()).add(result.source)

    relevant = [article for article in articles if _within_window(article, window) and is_relevant(article, topic)]
    groups = group_articles(relevant, topic.get("entities", {}).get("boost", []))

    items: list[Item] = []
    clusters: list[Cluster] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for idx, group in enumerate(groups, start=1):
        cluster_id = f"cl_{idx:04d}"
        member_urls = {canonicalize_url(a.url) for a in group}
        new_members = member_urls - set(seen)
        if not include_seen and not new_members:
            continue

        chosen = best_member(group)
        canonical = canonicalize_url(chosen.url)
        primary, secondary, confidence, matched_terms = classify(chosen, topic)
        focus_terms = topic.get("entities", {}).get("focus", [])
        focus_text = (chosen.title + " " + chosen.snippet).lower().replace("ё", "е")
        focus_hits = [str(term) for term in focus_terms if contains_term(focus_text, str(term))][:6]
        first_seen = seen.get(canonical, now)

        items.append(
            Item(
                item_id=f"it_{len(items) + 1:04d}",
                cluster_id=cluster_id,
                canonical_url=canonical,
                title_original=chosen.title,
                source=chosen.source,
                source_kind=chosen.source_kind,
                published_at=chosen.published_at or now,
                first_seen=first_seen,
                snippet=chosen.snippet,
                lang=chosen.lang,
                url_kind="google_redirect" if "news.google.com" in canonical else "direct",
                facets=Facets(
                    primary=primary,
                    secondary=secondary,
                    confidence=confidence,
                    matched_terms=matched_terms,
                ),
                entities=focus_hits,
                corroboration_count=len(group),
                matched_queries=sorted({a.query for a in group if a.query}),
                found_by=sorted(
                    {
                        origin
                        for article in group
                        for origin in origins.get(canonicalize_url(article.url), set())
                    }
                ),
            )
        )
        clusters.append(
            Cluster(
                cluster_id=cluster_id,
                story_key=min(member_urls),
                member_count=len(group),
                first_seen=min((a.published_at or now) for a in group),
                last_seen=max((a.published_at or now) for a in group),
                update_hash=str(len(member_urls)),
            )
        )

    return Digest(
        schema_version=1,
        run_id=run_id,
        topic=str(topic.get("id", "")),
        window=window,
        items=items,
        clusters=clusters,
        source_health=_aggregate_health(results),
    )


def collect(
    topic: dict,
    settings: dict,
    *,
    window_hours: int | None = None,
    progress=None,
) -> Digest:
    window_hours = int(window_hours or (topic.get("window", {}).get("hours") or DEFAULT_WINDOW_HOURS))
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)
    window = Window(
        start=start.isoformat(timespec="seconds"),
        end=now.isoformat(timespec="seconds"),
        tz=str(topic.get("timezone", "Europe/Moscow")),
        kind=str(topic.get("window", {}).get("kind", "rolling")),
    )
    run_id = _run_id(str(topic.get("id", "topic")))
    started_at = now.isoformat(timespec="seconds")

    state_db = Path(settings["paths"]["state_dir"]) / "intel.db"
    store = Store(state_db, str(topic.get("id", "topic")))
    store.start_run(run_id, started_at)
    try:
        if progress is not None:
            progress.stage(f"采集（窗口 {window_hours}h，{_count_tasks(topic)} 个查询/信源）")
        results = _collect_results(topic, settings, window_hours, progress=progress)
        if progress is not None:
            raw = sum(len(result.articles) for result in results)
            progress.finish(f"采集完成：{raw} 条原始结果，开始过滤/聚类")
            progress.stage("过滤、去重、事件聚类")
        seen = store.seen_urls()
        include_seen = bool(topic.get("window", {}).get("include_seen", False))
        digest = _build_digest(topic, run_id, window, results, seen, include_seen=include_seen)
        if progress is not None:
            progress.finish(f"入选 {len(digest.items)} 条，写入状态库")
        for item in digest.items:
            store.record_item(item)
        store.commit()
        store.record_health(run_id, digest.source_health)
        status = "ok" if all(h.ok for h in digest.source_health) else "partial"
        store.finish_run(
            run_id,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            status,
            {"items": len(digest.items), "sources": len(digest.source_health)},
        )
        return digest
    finally:
        store.close()


def load_digest(path: Path) -> Digest:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Digest.from_dict(data)
