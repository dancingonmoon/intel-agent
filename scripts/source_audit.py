#!/usr/bin/env python3
"""Audit recent source health and flag sources to keep/watch/remove."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path.home() / ".local/state/intel-agent/intel.db"


def audit(db_path: Path, topic: str, runs: int) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        run_ids = [
            row["run_id"]
            for row in conn.execute(
                "SELECT DISTINCT run_id FROM source_health WHERE run_id LIKE ? "
                "ORDER BY run_id DESC LIMIT ?",
                (f"%-{topic}", runs),
            )
        ]
        if not run_ids:
            return []
        marks = ",".join("?" for _ in run_ids)
        rows = conn.execute(
            f"""
            SELECT source,
                   COUNT(*) AS runs,
                   SUM(CASE WHEN ok THEN 1 ELSE 0 END) AS ok_runs,
                   SUM(count) AS items,
                   MAX(CASE WHEN error IS NOT NULL AND error <> '' THEN error END) AS last_error
            FROM source_health
            WHERE run_id IN ({marks})
            GROUP BY source
            """,
            run_ids,
        ).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        runs_seen = int(row["runs"])
        ok_runs = int(row["ok_runs"] or 0)
        items = int(row["items"] or 0)
        error = row["last_error"] or ""
        flaky = any(token in error.lower() for token in ("selector drift", "captcha", "cdp discovery"))
        if ok_runs == 0:
            verdict = "remove"
        elif items == 0 or ok_runs / max(runs_seen, 1) < 0.6 or flaky:
            verdict = "watch"
        else:
            verdict = "keep"
        result.append(
            {
                "source": row["source"],
                "runs": runs_seen,
                "ok_runs": ok_runs,
                "items": items,
                "verdict": verdict,
                "last_error": error[:160],
            }
        )
    order = {"remove": 0, "watch": 1, "keep": 2}
    result.sort(key=lambda row: (order[row["verdict"]], -row["items"], row["source"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit intel-agent source health")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--topic", default="ru-tetra")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = audit(Path(args.db).expanduser(), args.topic, args.runs)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("no source health rows")
        return 0
    print(f"# source audit: {args.topic}, last {args.runs} runs\n")
    print("| source | runs | ok | items | verdict | last error |")
    print("|---|---:|---:|---:|---|---|")
    for row in rows:
        error = row["last_error"].replace("|", "\\|")
        print(
            f"| {row['source']} | {row['runs']} | {row['ok_runs']} | "
            f"{row['items']} | {row['verdict']} | {error} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
