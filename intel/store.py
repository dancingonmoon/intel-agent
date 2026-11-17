"""SQLite state: seen items, runs, source health and delivery ledger."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import Item, SourceHealth

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA user_version=1;

CREATE TABLE IF NOT EXISTS items (
  topic TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  title_norm TEXT NOT NULL,
  title_original TEXT,
  source TEXT,
  source_kind TEXT,
  published_at TEXT,
  snippet TEXT,
  facet TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  last_published TEXT,
  seen_count INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (topic, canonical_url)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT,
  stats_json TEXT
);

CREATE TABLE IF NOT EXISTS source_health (
  run_id TEXT NOT NULL,
  source TEXT NOT NULL,
  ok INTEGER NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  http_status INTEGER,
  duration_ms INTEGER,
  error TEXT,
  PRIMARY KEY (run_id, source)
);

CREATE TABLE IF NOT EXISTS deliveries (
  topic TEXT NOT NULL,
  report_date TEXT NOT NULL,
  channel TEXT NOT NULL,
  content_hash TEXT,
  status TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT,
  PRIMARY KEY (topic, report_date, channel)
);

CREATE TABLE IF NOT EXISTS source_feedback (
  topic TEXT NOT NULL,
  report_date TEXT,
  item_id TEXT,
  source TEXT NOT NULL,
  verdict TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_selections (
  topic TEXT NOT NULL,
  report_date TEXT NOT NULL,
  item_id TEXT NOT NULL,
  source TEXT,
  canonical_url TEXT,
  title_original TEXT,
  facet TEXT,
  found_by TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (topic, report_date, item_id)
);
"""


class Store:
    def __init__(self, path: Path, topic: str):
        self.path = Path(path)
        self.topic = topic
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first local DB was created."""
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(items)")}
        additions = {
            "title_original": "TEXT",
            "source": "TEXT",
            "source_kind": "TEXT",
            "published_at": "TEXT",
            "snippet": "TEXT",
            "facet": "TEXT",
        }
        for name, decl in additions.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE items ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self.conn.close()

    def seen_urls(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT canonical_url, first_seen FROM items WHERE topic = ?", (self.topic,)
        ).fetchall()
        return {row["canonical_url"]: row["first_seen"] for row in rows}

    def week_items(self, days: int = 7, limit: int = 30) -> list[dict]:
        """Items first seen in the last N days, for the weekly background."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            """
            SELECT canonical_url, title_original, source, source_kind, published_at,
                   first_seen, snippet, facet
            FROM items
            WHERE topic = ? AND first_seen >= ?
              AND title_original IS NOT NULL AND title_original <> ''
            ORDER BY COALESCE(NULLIF(published_at, ''), first_seen) DESC
            LIMIT ?
            """,
            (self.topic, cutoff, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_item(self, item: Item) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO items (
              topic, canonical_url, title_norm, title_original, source, source_kind,
              published_at, snippet, facet, first_seen, last_seen, last_published, seen_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(topic, canonical_url) DO UPDATE SET
              title_original = excluded.title_original,
              source = excluded.source,
              source_kind = excluded.source_kind,
              published_at = excluded.published_at,
              snippet = excluded.snippet,
              facet = excluded.facet,
              last_seen = excluded.last_seen,
              last_published = excluded.last_published,
              seen_count = seen_count + 1
            """,
            (
                self.topic,
                item.canonical_url,
                item.title_original.lower(),
                item.title_original,
                item.source,
                item.source_kind,
                item.published_at,
                item.snippet[:1000],
                item.facets.primary,
                item.first_seen,
                now,
                item.published_at,
            ),
        )

    def commit(self) -> None:
        self.conn.commit()

    def start_run(self, run_id: str, started_at: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, topic, started_at, status) VALUES (?, ?, ?, 'running')",
            (run_id, self.topic, started_at),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, finished_at: str, status: str, stats: dict) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, stats_json = ? WHERE run_id = ?",
            (finished_at, status, json.dumps(stats, ensure_ascii=False), run_id),
        )
        self.conn.commit()

    def record_health(self, run_id: str, health: list[SourceHealth]) -> None:
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO source_health (run_id, source, ok, count, http_status, duration_ms, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (run_id, h.source, 1 if h.ok else 0, h.count, h.http_status, h.duration_ms, h.error)
                for h in health
            ],
        )
        self.conn.commit()

    def delivered(self, report_date: str, channel: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM deliveries WHERE topic = ? AND report_date = ? AND channel = ?",
            (self.topic, report_date, channel),
        ).fetchone()
        return bool(row and row["status"] == "ok")

    def mark_delivery(self, report_date: str, channel: str, status: str, content_hash: str = "", error: str = "") -> None:
        self.conn.execute(
            """
            INSERT INTO deliveries (topic, report_date, channel, content_hash, status, attempts, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, datetime('now'))
            ON CONFLICT(topic, report_date, channel) DO UPDATE SET
              content_hash = excluded.content_hash,
              status = excluded.status,
              attempts = attempts + 1,
              last_error = excluded.last_error,
              updated_at = datetime('now')
            """,
            (self.topic, report_date, channel, content_hash, status, error),
        )
        self.conn.commit()

    def last_success(self) -> str | None:
        row = self.conn.execute(
            "SELECT finished_at FROM runs WHERE topic = ? AND status IN ('ok', 'partial') AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1",
            (self.topic,),
        ).fetchone()
        return row["finished_at"] if row else None

    def record_selection(self, report_date: str, rows: list[dict]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO report_selections
              (topic, report_date, item_id, source, canonical_url, title_original,
               facet, found_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    self.topic,
                    report_date,
                    str(row.get("item_id", "")),
                    str(row.get("source", "")),
                    str(row.get("canonical_url", "")),
                    str(row.get("title_original", "")),
                    str(row.get("facet", "")),
                    json.dumps(row.get("found_by", []), ensure_ascii=False),
                    now,
                )
                for row in rows
                if row.get("item_id")
            ],
        )
        self.conn.commit()

    def record_feedback(
        self,
        source: str,
        verdict: str,
        *,
        report_date: str = "",
        item_id: str = "",
        note: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute(
            """
            INSERT INTO source_feedback
              (topic, report_date, item_id, source, verdict, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (self.topic, report_date, item_id, source, verdict, note[:500], now),
        )
        self.conn.commit()

    def selection_counts(self, days: int = 30) -> dict[str, dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        rows = self.conn.execute(
            """
            SELECT found_by, COUNT(*) AS n
            FROM report_selections
            WHERE topic = ? AND report_date >= ?
            GROUP BY found_by
            """,
            (self.topic, cutoff),
        ).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            try:
                sources = json.loads(row["found_by"] or "[]")
            except json.JSONDecodeError:
                sources = []
            for source in sources:
                bucket = result.setdefault(str(source), {"selected": 0})
                bucket["selected"] += int(row["n"])
        return result

    def feedback_counts(self, days: int = 30) -> dict[str, dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            """
            SELECT source,
                   SUM(CASE WHEN verdict = 'good' THEN 1 ELSE 0 END) AS good,
                   SUM(CASE WHEN verdict = 'bad' THEN 1 ELSE 0 END) AS bad
            FROM source_feedback
            WHERE topic = ? AND created_at >= ?
            GROUP BY source
            """,
            (self.topic, cutoff),
        ).fetchall()
        return {
            row["source"]: {"good": int(row["good"] or 0), "bad": int(row["bad"] or 0)}
            for row in rows
        }
