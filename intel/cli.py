"""Command-line entry point for intel-agent."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import CONFIG_DIR, TOPICS_DIR, ensure_dirs, load_glossary, load_settings, load_topic
from .contracts import Digest, Report, validate_report_data
from .deep_research import DEFAULT_WINDOW_HOURS, run as deep_research_run
from .sources import agent_browser
from .doctor import render_checks, run_checks
from .learning import (
    add_sources,
    disable_sources,
    enable_sources,
    learn,
    load_overlay,
    overlay_path,
    source_metrics,
    suggest_sources,
    test_rss,
    test_telegram,
)
from .llm import summarize
from .pipeline import PipelineNotReady, collect, load_digest
from .relevance import is_relevant
from .render import build_fallback_report, render_markdown
from .sources.base import Article
from .store import Store

FIXTURE_DIGEST = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "digest.sample.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_digest(args, settings: dict, topic: dict) -> Digest:
    if args.digest:
        return load_digest(Path(args.digest))
    if args.fake:
        return load_digest(FIXTURE_DIGEST)
    return collect(topic, settings, window_hours=args.window_hours or None)


def _week_rows(rows: list[dict], today: set[str]) -> list[dict]:
    fresh = [row for row in rows if row.get("canonical_url") not in today]
    # A rolling 7-day report may have only one event, and it is also today's
    # digest item. Keep it rather than telling the LLM there is no background.
    return fresh or rows


def _week_row_relevant(row: dict, topic: dict) -> bool:
    """Re-apply today's relevance rules so old false positives do not leak in."""
    article = Article(
        title=str(row.get("title_original") or ""),
        url=str(row.get("canonical_url") or ""),
        source=str(row.get("source") or ""),
        source_kind=str(row.get("source_kind") or "media"),
        published_at=str(row.get("published_at") or ""),
        snippet=str(row.get("snippet") or ""),
        lang="",
    )
    return is_relevant(article, topic)


def _week_background(settings: dict, topic: dict, digest: Digest) -> list[dict]:
    """Last 7 days of stored items; prefer items not already in today's digest."""
    store = Store(Path(settings["paths"]["state_dir"]) / "intel.db", str(topic["id"]))
    try:
        rows = [row for row in store.week_items(days=7) if _week_row_relevant(row, topic)]
    finally:
        store.close()
    today = {item.canonical_url for item in digest.items}
    return _week_rows(rows, today)


def _load_report(args, digest: Digest, topic: dict, settings: dict, glossary: dict) -> tuple[Report, str]:
    if args.report:
        data = _read_json(Path(args.report))
        errors = validate_report_data(data, digest)
        if errors:
            raise SystemExit("report validation failed:\n- " + "\n- ".join(errors))
        return Report.from_dict(data), "file"
    if args.llm_provider:
        settings = json.loads(json.dumps(settings))
        settings["llm"]["provider"] = args.llm_provider
    week_items = _week_background(settings, topic, digest)
    return summarize(digest, topic, settings, glossary, week_items=week_items)


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    topic = load_topic(args.topic, Path(args.topic_config) if args.topic_config else None)
    ensure_dirs(settings)
    glossary = load_glossary()
    if not args.digest and not getattr(args, "fake", False):
        cdp_ok, cdp_detail = agent_browser.ensure_cdp(settings)
        if not cdp_ok:
            print(f"warning: {cdp_detail}", file=sys.stderr)

    digest = _load_digest(args, settings, topic)
    digest_path = Path(settings["paths"]["state_dir"]) / "runs" / digest.run_id / "digest.json"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(
        json.dumps(dataclasses.asdict(digest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"digest written: {digest_path}")
    core_ok = sum(1 for health in digest.source_health if health.ok)
    if not digest.items and core_ok < 2:
        print(
            "error: no items and fewer than 2 healthy sources; refusing to deliver an empty report",
            file=sys.stderr,
        )
        return 5
    report, status = _load_report(args, digest, topic, settings, glossary)
    markdown = render_markdown(digest, report, topic)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        path = out
    else:
        path = Path(settings["paths"]["reports_dir"]) / str(topic["id"]) / f"{report.report_date}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")

    print(f"report written: {path}")
    print(f"summary status: {status}")

    store = Store(Path(settings["paths"]["state_dir"]) / "intel.db", str(topic["id"]))
    try:
        items = digest.items_by_id
        selected_rows = []
        for facet, entries in report.sections.items():
            for entry in entries:
                item = items.get(entry.item_id)
                if not item:
                    continue
                selected_rows.append(
                    {
                        "item_id": item.item_id,
                        "source": item.source,
                        "canonical_url": item.canonical_url,
                        "title_original": item.title_original,
                        "facet": facet,
                        "found_by": item.found_by,
                    }
                )
        store.record_selection(report.report_date, selected_rows)
        learning_cfg = settings.get("sources", {}).get("learning", {})
        if selected_rows and learning_cfg.get("enabled") and learning_cfg.get("auto_apply"):
            try:
                learn(
                    store,
                    str(topic["id"]),
                    settings,
                    runs=int(learning_cfg.get("runs", 10)),
                    auto_disable=True,
                    auto_add=True,
                )
            except Exception:
                # Source learning must never block report delivery.
                pass

        if args.no_deliver:
            return 0 if not status.startswith("degraded") else 2

        send_settings = copy.deepcopy(settings)
        if not args.force_deliver:
            if store.delivered(report.report_date, "wechat"):
                send_settings["delivery"]["wechat"]["enabled"] = False
            if store.delivered(report.report_date, "email"):
                send_settings["delivery"]["email"]["enabled"] = False
        results = deliver(
            markdown,
            digest,
            report,
            topic,
            send_settings,
            dry_run=bool(args.dry_run),
            enable_all=bool(args.deliver_all),
        )
        if not args.dry_run:
            for channel in ("wechat", "email", "feishu"):
                outcome = results.get(channel, {})
                if outcome.get("skipped"):
                    continue
                store.mark_delivery(
                    report.report_date,
                    channel,
                    "ok" if outcome.get("ok") else "failed",
                    error=str(
                        outcome.get("output")
                        or outcome.get("error")
                        or outcome.get("note")
                        or ""
                    )[:500],
                )
    finally:
        store.close()

    print(json.dumps(results, ensure_ascii=False, indent=2))

    failed = any(not item.get("ok", True) for item in results.values())
    # Feishu primary failing is not a delivery failure if the WeChat fallback worked.
    if results.get("feishu", {}).get("fallback_to_wechat") and results.get("wechat", {}).get("ok"):
        failed = False
    if failed:
        return 3
    return 0 if not status.startswith("degraded") else 2


def cmd_render(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    topic = load_topic(args.topic, Path(args.topic_config) if args.topic_config else None)
    digest = load_digest(Path(args.digest))
    if args.report:
        data = _read_json(Path(args.report))
        errors = validate_report_data(data, digest)
        if errors:
            raise SystemExit("report validation failed:\n- " + "\n- ".join(errors))
        report = Report.from_dict(data)
    else:
        report = build_fallback_report(digest, topic, reason="render-without-report")
    markdown = render_markdown(digest, report, topic)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"report written: {out}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    digest = load_digest(Path(args.digest))
    data = _read_json(Path(args.report))
    errors = validate_report_data(data, digest)
    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 2
    print("VALID")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    checks = run_checks(settings)
    print(render_checks(checks))
    return 0 if all(check["ok"] or check.get("optional") for check in checks) else 2


def cmd_collect(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    topic = load_topic(args.topic, Path(args.topic_config) if args.topic_config else None)
    ensure_dirs(settings)
    cdp_ok, cdp_detail = agent_browser.ensure_cdp(settings)
    if not cdp_ok:
        print(f"warning: {cdp_detail}", file=sys.stderr)
    digest = collect(topic, settings, window_hours=args.window_hours or None)
    if args.out:
        out = Path(args.out)
    else:
        out = Path(settings["paths"]["state_dir"]) / "runs" / digest.run_id / "digest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataclasses.asdict(digest), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"digest written: {out}")
    print(f"items: {len(digest.items)}")
    for health in digest.source_health:
        print(f"  {health.source}: ok={health.ok} count={health.count} error={health.error or ''}")
    return 0 if all(health.ok for health in digest.source_health) else 2


def cmd_research(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    if args.llm_provider:
        settings["llm"]["provider"] = args.llm_provider
    topic = load_topic(args.topic, Path(args.topic_config) if args.topic_config else None)
    ensure_dirs(settings)
    out = deep_research_run(
        topic,
        settings,
        args.question,
        window_hours=args.window_hours or DEFAULT_WINDOW_HOURS,
    )
    print(f"research written: {out}")
    print(f"evidence written: {out.with_suffix('.evidence.json')}")
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    if args.good == args.bad:
        print("error: pass exactly one of --good or --bad", file=sys.stderr)
        return 2
    verdict = "good" if args.good else "bad"
    settings = load_settings(Path(args.settings) if args.settings else None)
    store = Store(Path(settings["paths"]["state_dir"]) / "intel.db", str(args.topic))
    try:
        sources: list[str] = []
        if args.source:
            sources = [args.source]
        elif args.item:
            query = "SELECT source, found_by FROM report_selections WHERE topic = ? AND item_id = ?"
            params: list[str] = [str(args.topic), str(args.item)]
            if args.date:
                query += " AND report_date = ?"
                params.append(str(args.date))
            query += " ORDER BY report_date DESC LIMIT 1"
            row = store.conn.execute(query, params).fetchone()
            if row:
                try:
                    sources = [str(x) for x in json.loads(row["found_by"] or "[]")]
                except json.JSONDecodeError:
                    sources = []
                if not sources and row["source"]:
                    sources = [str(row["source"])]
        if not sources:
            print("error: no matching item/source; pass --source or a valid --item", file=sys.stderr)
            return 2
        for source in sources:
            store.record_feedback(
                source,
                verdict,
                report_date=args.date,
                item_id=args.item,
                note=args.note,
            )
        print(json.dumps({"ok": True, "sources": sources, "verdict": verdict}, ensure_ascii=False, indent=2))
        return 0
    finally:
        store.close()


def cmd_source_test(args: argparse.Namespace) -> int:
    if bool(args.rss) == bool(args.telegram):
        print("error: pass exactly one of --rss or --telegram", file=sys.stderr)
        return 2
    settings = load_settings(Path(args.settings) if args.settings else None)
    if args.rss:
        ok, count, error = test_rss(args.rss, settings)
        kind, value = "rss", args.rss
    else:
        ok, count, error = test_telegram(args.telegram, settings)
        kind, value = "telegram", args.telegram
    print(json.dumps({"kind": kind, "value": value, "ok": ok, "items": count, "error": error}, ensure_ascii=False, indent=2))
    return 0 if ok and count else 2


def cmd_source_add(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    rss_urls = list(args.rss or [])
    channels = list(args.telegram or [])
    failures = []
    if not args.force:
        for url in rss_urls:
            ok, count, error = test_rss(url, settings)
            if not (ok and count):
                failures.append(f"rss {url}: {error or 'no items'}")
        for channel in channels:
            ok, count, error = test_telegram(channel, settings)
            if not (ok and count):
                failures.append(f"telegram {channel}: {error or 'no items'}")
    if failures:
        print("source validation failed:\n- " + "\n- ".join(failures), file=sys.stderr)
        return 2
    path = add_sources(str(args.topic), rss_urls=rss_urls, channels=channels)
    print(f"updated: {path}")
    return 0


def cmd_source_disable(args: argparse.Namespace) -> int:
    path = disable_sources(str(args.topic), list(args.source or []))
    print(f"updated: {path}")
    return 0


def cmd_source_enable(args: argparse.Namespace) -> int:
    path = enable_sources(str(args.topic), list(args.source or []))
    print(f"updated: {path}")
    return 0


def cmd_source_list(args: argparse.Namespace) -> int:
    topic = load_topic(args.topic, Path(args.topic_config) if args.topic_config else None)
    queries = topic.get("queries", {})
    print(f"topic: {topic['name']} ({topic['id']})")
    print(f"overlay: {overlay_path(str(topic['id']))}")
    print(f"rss: {len(queries.get('rss') or [])}")
    for url in queries.get("rss") or []:
        print(f"  {url}")
    print(f"telegram: {len(queries.get('telegram_channels') or [])}")
    for channel in queries.get("telegram_channels") or []:
        print(f"  telegram:@{channel}")
    disabled = topic.get("disabled_sources") or []
    print(f"disabled: {len(disabled)}")
    for source in disabled:
        print(f"  {source}")
    return 0


def cmd_source_audit(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    store = Store(Path(settings["paths"]["state_dir"]) / "intel.db", str(args.topic))
    try:
        rows = source_metrics(store, str(args.topic), runs=args.runs)
    finally:
        store.close()
    if args.json:
        print(json.dumps([row.as_dict() for row in rows], ensure_ascii=False, indent=2))
        return 0
    print(f"# source audit: {args.topic}, last {args.runs} runs\n")
    print("| source | runs | ok | fetched | selected | good | bad | verdict | last error |")
    print("|---|---:|---:|---:|---:|---:|---:|---|---|")
    for row in rows:
        error = row.last_error.replace("|", "\\|")
        print(
            f"| {row.source} | {row.runs} | {row.ok_runs} | {row.fetched} | {row.selected} | "
            f"{row.good} | {row.bad} | {row.verdict} | {error} |"
        )
    return 0


def cmd_source_suggest(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    store = Store(Path(settings["paths"]["state_dir"]) / "intel.db", str(args.topic))
    try:
        suggestions = suggest_sources(store, str(args.topic), settings, days=args.days)
    finally:
        store.close()
    print(json.dumps(suggestions, ensure_ascii=False, indent=2))
    return 0


def cmd_source_learn(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.settings) if args.settings else None)
    store = Store(Path(settings["paths"]["state_dir"]) / "intel.db", str(args.topic))
    try:
        result = learn(
            store,
            str(args.topic),
            settings,
            runs=args.runs,
            auto_disable=bool(args.apply),
            auto_add=bool(args.apply),
        )
    finally:
        store.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    settings_path = Path(args.settings) if args.settings else (CONFIG_DIR / "settings.yaml")
    example = CONFIG_DIR / "settings.example.yaml"
    if settings_path.exists() and not args.force:
        print(f"settings already exists: {settings_path} (use --force to overwrite)", file=sys.stderr)
        return 2
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example, settings_path)
    overlay = TOPICS_DIR / "ru-tetra.sources.yaml"
    if not overlay.exists():
        overlay.write_text(
            "# User/learned source overlay. The base topic YAML is never edited.\n"
            "add:\n  rss: []\n  telegram_channels: []\n"
            "disable:\n  sources: []\n",
            encoding="utf-8",
        )
    settings = load_settings(settings_path)
    ensure_dirs(settings)
    print(f"settings written: {settings_path}")
    print(f"topic overlay: {overlay}")
    print("next: intel doctor")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="intel", description="Daily multilingual industry intelligence")
    parser.add_argument("--version", action="version", version=f"intel-agent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create config/settings.yaml from the commented example")
    init.add_argument("--settings", default="")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    run = sub.add_parser("run", help="collect (or load), summarize, render and deliver")
    run.add_argument("--topic", required=True)
    run.add_argument("--topic-config", default="")
    run.add_argument("--settings", default="")
    run.add_argument("--digest", default="", help="use an existing digest.json instead of collecting")
    run.add_argument("--report", default="", help="use an existing report.json instead of calling the LLM")
    run.add_argument("--fake", action="store_true", help="use the bundled sample digest")
    run.add_argument("--window-hours", type=int, default=0, help="collection window; default from topic")
    run.add_argument("--llm-provider", choices=["auto", "none", "claude-cli", "anthropic"], default="")
    run.add_argument("--out", default="", help="write the Markdown here instead of the reports dir")
    run.add_argument("--no-deliver", action="store_true")
    run.add_argument("--dry-run", action="store_true", help="print delivery payloads without sending")
    run.add_argument("--deliver-all", action="store_true", help="force WeChat and email even if disabled in config")
    run.add_argument("--force-deliver", action="store_true", help="ignore the per-day delivery ledger")
    run.set_defaults(func=cmd_run)

    collect_cmd = sub.add_parser("collect", help="run source adapters and write digest.json")
    collect_cmd.add_argument("--topic", required=True)
    collect_cmd.add_argument("--topic-config", default="")
    collect_cmd.add_argument("--settings", default="")
    collect_cmd.add_argument("--window-hours", type=int, default=0)
    collect_cmd.add_argument("--out", default="")
    collect_cmd.set_defaults(func=cmd_collect)

    research = sub.add_parser("research", help="deep research: long window, evidence table, long report")
    research.add_argument("--topic", required=True)
    research.add_argument("--question", required=True)
    research.add_argument("--topic-config", default="")
    research.add_argument("--settings", default="")
    research.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS)
    research.add_argument("--llm-provider", choices=["auto", "none", "claude-cli", "anthropic"], default="")
    research.set_defaults(func=cmd_research)

    render = sub.add_parser("render", help="render Markdown from digest + optional report")
    render.add_argument("--topic", required=True)
    render.add_argument("--topic-config", default="")
    render.add_argument("--settings", default="")
    render.add_argument("--digest", required=True)
    render.add_argument("--report", default="")
    render.add_argument("--out", required=True)
    render.set_defaults(func=cmd_render)

    validate = sub.add_parser("validate-report", help="validate report.json against digest.json")
    validate.add_argument("--digest", required=True)
    validate.add_argument("--report", required=True)
    validate.set_defaults(func=cmd_validate)

    doctor = sub.add_parser("doctor", help="check proxy, LLM, browser, delivery and directories")
    doctor.add_argument("--settings", default="")
    doctor.set_defaults(func=cmd_doctor)

    feedback = sub.add_parser("feedback", help="record good/bad feedback for an item or source")
    feedback.add_argument("--topic", required=True)
    feedback.add_argument("--item", default="", help="item_id from the report")
    feedback.add_argument("--source", default="", help="source id from the coverage section")
    feedback.add_argument("--date", default="", help="report date; default latest selection")
    feedback.add_argument("--good", action="store_true")
    feedback.add_argument("--bad", action="store_true")
    feedback.add_argument("--note", default="")
    feedback.add_argument("--settings", default="")
    feedback.set_defaults(func=cmd_feedback)

    source = sub.add_parser("source", help="test, add, disable and audit sources")
    source_sub = source.add_subparsers(dest="source_command", required=True)

    source_test = source_sub.add_parser("test", help="fetch a candidate RSS/Telegram source")
    source_test.add_argument("--rss", default="")
    source_test.add_argument("--telegram", default="")
    source_test.add_argument("--settings", default="")
    source_test.set_defaults(func=cmd_source_test)

    source_add = source_sub.add_parser("add", help="validate and add sources to the topic overlay")
    source_add.add_argument("--topic", required=True)
    source_add.add_argument("--rss", action="append", default=[])
    source_add.add_argument("--telegram", action="append", default=[])
    source_add.add_argument("--force", action="store_true", help="add even if the test fetch fails")
    source_add.add_argument("--settings", default="")
    source_add.set_defaults(func=cmd_source_add)

    source_disable = source_sub.add_parser("disable", help="disable sources in the topic overlay")
    source_disable.add_argument("--topic", required=True)
    source_disable.add_argument("--source", action="append", required=True)
    source_disable.set_defaults(func=cmd_source_disable)

    source_enable = source_sub.add_parser("enable", help="re-enable sources in the topic overlay")
    source_enable.add_argument("--topic", required=True)
    source_enable.add_argument("--source", action="append", required=True)
    source_enable.set_defaults(func=cmd_source_enable)

    source_list = source_sub.add_parser("list", help="list configured and disabled sources")
    source_list.add_argument("--topic", required=True)
    source_list.add_argument("--topic-config", default="")
    source_list.set_defaults(func=cmd_source_list)

    source_audit = source_sub.add_parser("audit", help="score sources from health, selections and feedback")
    source_audit.add_argument("--topic", required=True)
    source_audit.add_argument("--runs", type=int, default=10)
    source_audit.add_argument("--settings", default="")
    source_audit.add_argument("--json", action="store_true")
    source_audit.set_defaults(func=cmd_source_audit)

    source_suggest = source_sub.add_parser("suggest", help="suggest new sources cited by selected reports")
    source_suggest.add_argument("--topic", required=True)
    source_suggest.add_argument("--days", type=int, default=30)
    source_suggest.add_argument("--settings", default="")
    source_suggest.set_defaults(func=cmd_source_suggest)

    source_learn = source_sub.add_parser("learn", help="apply feedback-driven source learning")
    source_learn.add_argument("--topic", required=True)
    source_learn.add_argument("--runs", type=int, default=10)
    source_learn.add_argument("--apply", action="store_true", help="disable weak sources and add validated suggestions")
    source_learn.add_argument("--settings", default="")
    source_learn.set_defaults(func=cmd_source_learn)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PipelineNotReady as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
