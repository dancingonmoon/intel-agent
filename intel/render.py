"""Render a report from the digest + report contract.

All citations (URL, original title, source, date, confidence) are resolved
here from `digest.json`. `report.json` only supplies item ids and Chinese prose.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .contracts import (
    FACETS,
    FACET_LABELS,
    Coverage,
    Digest,
    Item,
    Report,
    ReportItem,
)


def _fmt_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return value[:10] if len(value) >= 10 and value[4] == "-" else value


def _window_period(digest: Digest) -> str:
    """Use 今日 for a daily window, 本周 for a rolling multi-day window."""
    try:
        start = datetime.fromisoformat(digest.window.start)
        end = datetime.fromisoformat(digest.window.end)
        if start.tzinfo is None:
            start = start.replace(tzinfo=end.tzinfo)
        hours = (end - start).total_seconds() / 3600
    except (TypeError, ValueError):
        return "今日"
    return "本周" if hours >= 48 else "今日"


def _needs_translation(text: str) -> bool:
    """True for non-Chinese, non-ASCII-letter titles (e.g. Russian)."""
    for ch in text or "":
        if ch.isalpha() and ord(ch) > 127 and not ("一" <= ch <= "鿿"):
            return True
    return False


def _display_title(item: Item, report: Report, fallback: str = "") -> str:
    if not _needs_translation(item.title_original):
        return item.title_original
    translated = (report.title_en.get(item.item_id) or "").strip()
    if translated:
        return translated
    if fallback.strip():
        return fallback
    for entries in report.sections.values():
        for entry in entries:
            if entry.item_id == item.item_id and entry.title_zh.strip():
                return entry.title_zh
    return item.title_original


def _original_display(item: Item, report: Report, fallback: str = "") -> tuple[str, str]:
    if not _needs_translation(item.title_original):
        return "原文", item.title_original
    translated = (report.title_en.get(item.item_id) or "").strip()
    if translated:
        return "原文（英译）", translated
    if fallback.strip():
        return "原文（中文译名）", fallback
    return "原文", item.title_original


_CYRILLIC_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}
_URL_SPLIT_RE = re.compile(r"(https?://\S+)")


def _transliterate_cyrillic(text: str) -> str:
    def repl(match: re.Match) -> str:
        out = []
        for ch in match.group(0):
            mapped = _CYRILLIC_LATIN.get(ch.lower())
            if mapped is None:
                out.append(ch)
            elif ch.isupper() and mapped:
                out.append(mapped[0].upper() + mapped[1:])
            else:
                out.append(mapped)
        return "".join(out)

    return re.sub(r"[Ѐ-ӿ]+", repl, text)


def _guard_display_text(text: str) -> str:
    """Final-output guard: never show Cyrillic; leave URLs untouched."""
    parts = _URL_SPLIT_RE.split(text)
    return "".join(
        part if part.startswith(("http://", "https://")) else _transliterate_cyrillic(part)
        for part in parts
    )


def _source_health_coverage(digest: Digest) -> Coverage:
    ok: list[str] = []
    degraded: list[str] = []
    failed: list[str] = []
    for health in digest.source_health:
        if health.ok and not health.degraded_reason:
            ok.append(health.source)
        elif health.ok and health.degraded_reason:
            degraded.append(health.source)
        else:
            failed.append(health.source)
    return Coverage(ok=ok, degraded=degraded, failed=failed)


def build_fallback_report(
    digest: Digest, topic: dict, reason: str = "llm-unavailable", week_items: list[dict] | None = None
) -> Report:
    """Deterministic report used when the LLM is unavailable or invalid."""
    by_facet: dict[str, list[Item]] = {facet: [] for facet in FACETS}
    for item in digest.items:
        primary = item.facets.primary if item.facets.primary in FACETS else "other"
        if primary in by_facet:
            by_facet[primary].append(item)

    sections: dict[str, list[ReportItem]] = {}
    no_news: list[str] = []
    for facet in FACETS:
        entries: list[ReportItem] = []
        for item in by_facet[facet][:10]:
            summary = item.snippet.strip() or "（原文摘要不可用，请点击原文查看）"
            entries.append(
                ReportItem(
                    item_id=item.item_id,
                    title_zh=item.title_original,
                    summary_zh=summary,
                    why_it_matters="（自动摘要不可用，本条为原文摘录）",
                )
            )
        sections[facet] = entries
        if not entries:
            no_news.append(facet)

    headline = [item.item_id for item in digest.items[:5]]
    watchlist = [
        {"cluster_id": cluster.cluster_id, "reason": f"已有 {cluster.member_count} 家来源报道，持续跟踪"}
        for cluster in digest.clusters
        if cluster.member_count >= 2
    ][:10]

    coverage = _source_health_coverage(digest)
    coverage.no_news_facets = no_news
    week_review: list[str] = []
    for row in (week_items or [])[:5]:
        title = str(row.get("title_original") or row.get("title") or "").strip()
        if not title:
            continue
        source = str(row.get("source") or "").strip()
        day = str(row.get("published_at") or row.get("first_seen") or "")[:10]
        line = f"{day} {title}".strip()
        if source:
            line += f"（{source}）"
        week_review.append(line[:300])
    if not week_review:
        week_review = [f"过去 7 天暂无额外入库条目；今日采集 {len(digest.items)} 条。"]
    return Report(
        schema_version=1,
        run_id=digest.run_id,
        report_date=_fmt_date(digest.window.end) or date.today().isoformat(),
        headline_item_ids=headline,
        sections=sections,
        watchlist=watchlist,
        coverage=coverage,
        week_review=week_review,
        degraded=reason,
    )


def _render_item(entry: ReportItem, item: Item, digest: Digest, report: Report) -> list[str]:
    lines = [f"### {entry.title_zh}".rstrip(), ""]
    label, original = _original_display(item, report, entry.title_zh)
    lines.append(f"- **{label}**：{original}")
    lines.append(f"- **来源/日期**：{item.source} · {_fmt_date(item.published_at)}")
    if item.url_kind == "google_redirect":
        lines.append(f"- **链接（Google News 跳转）**：{item.canonical_url}")
    else:
        lines.append(f"- **链接**：{item.canonical_url}")
    lines.append(f"- **摘要**：{entry.summary_zh}")
    if entry.why_it_matters:
        lines.append(f"- **影响判断**：{entry.why_it_matters}")
    lines.append(f"- **可信度**：{_confidence_zh(item.facets.confidence)}")
    if item.facets.secondary:
        labels = [FACET_LABELS.get(f, f) for f in item.facets.secondary]
        lines.append(f"- **另见**：{'、'.join(labels)}")
    cluster = digest.clusters_by_id.get(item.cluster_id)
    if cluster and cluster.member_count > 1:
        lines.append(f"- **另有 {cluster.member_count - 1} 家报道**")
    lines.append("")
    return lines


def _confidence_zh(value: str) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(value, value or "中")


def _letter(facet: str) -> str:
    return {
        "contracts": "三",
        "industry": "四",
        "technology": "五",
        "players": "六",
    }.get(facet, "")


def render_markdown(digest: Digest, report: Report, topic: dict) -> str:
    items = digest.items_by_id
    selected = sum(len(entries) for entries in report.sections.values())
    lines: list[str] = []
    title = topic.get("name", digest.topic)
    lines.append(f"# {title} · 每日情报 {report.report_date}")
    lines.append("")
    window = digest.window
    period = _window_period(digest)
    lines.append(f"> 运行时间：{_fmt_date(window.end)}（{window.tz}）")
    lines.append(f"> 覆盖窗口：{window.start} – {window.end}（{window.kind}）")
    if digest.source_health:
        sources = " / ".join(h.source for h in digest.source_health)
        lines.append(f"> 来源：{sources}")
    lines.append(f"> 条目：采集 {len(digest.items)}，入选 {selected}")
    if report.degraded:
        lines.append("")
        lines.append(f"> ⚠️ 自动摘要不可用（{report.degraded}），本报告为确定性模板输出。")
    lines.append("")

    lines.append(f"## 一、{period}要点")
    lines.append("")
    if report.headline_item_ids:
        for idx, item_id in enumerate(report.headline_item_ids[:5], start=1):
            item = items.get(item_id)
            if not item:
                continue
            lines.append(f"{idx}. [{_display_title(item, report)}]({item.canonical_url}) — {item.source}")
    else:
        lines.append(f"{period}无入选条目。")
    lines.append("")

    lines.append("## 二、本周回顾")
    lines.append("")
    if report.week_review:
        for entry in report.week_review:
            lines.append(f"- {entry}")
    else:
        lines.append("本周暂无足够数据。")
    lines.append("")

    for facet in FACETS:
        lines.append(f"## {_letter(facet)}、{FACET_LABELS[facet]}")
        lines.append("")
        entries = report.sections.get(facet, [])
        if not entries:
            lines.append(f"{period}无{FACET_LABELS[facet]}相关内容。")
            lines.append("")
            continue
        for entry in entries:
            item = items.get(entry.item_id)
            if not item:
                continue
            lines.extend(_render_item(entry, item, digest, report))

    lines.append("## 七、值得继续跟踪")
    lines.append("")
    if report.watchlist:
        for entry in report.watchlist:
            cluster_id = entry.get("cluster_id")
            reason = str(entry.get("reason", "")).strip()
            cluster = digest.clusters_by_id.get(str(cluster_id))
            if not cluster:
                continue
            members = [i for i in digest.items if i.cluster_id == cluster.cluster_id]
            member_titles = "；".join(_display_title(i, report) for i in members[:3])
            suffix = f"（另有 {cluster.member_count - len(members[:3])} 篇）" if cluster.member_count > len(members[:3]) else ""
            lines.append(f"- {member_titles}{suffix}" + (f" — {reason}" if reason else ""))
    else:
        lines.append("暂无需要持续跟踪的条目。")
    lines.append("")

    lines.append("## 八、覆盖与缺口")
    lines.append("")
    coverage = report.coverage
    if coverage.ok:
        lines.append(f"- 成功来源：{', '.join(coverage.ok)}")
    if coverage.degraded:
        lines.append(f"- 降级来源：{', '.join(coverage.degraded)}")
    if coverage.failed:
        lines.append(f"- 失败来源：{', '.join(coverage.failed)}")
    if coverage.no_news_facets:
        labels = [FACET_LABELS.get(f, f) for f in coverage.no_news_facets]
        lines.append(f"- {period}无更新的维度：{'、'.join(labels)}")
    if not digest.source_health:
        lines.append("- 无来源健康数据。")
    lines.append("")
    return "\n".join(_guard_display_text(line) for line in lines).rstrip() + "\n"
