import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from intel.contracts import Facets, Item
from intel.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "intel.db", "ru-tetra")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_seen_and_delivery_ledger(self):
        item = Item(
            item_id="it_0001",
            cluster_id="cl_0001",
            canonical_url="https://example.com/a",
            title_original="TETRA Россия",
            source="Media",
            source_kind="media",
            published_at="2026-04-10T10:00:00+03:00",
            first_seen="2026-04-10T10:00:00+03:00",
            facets=Facets(primary="technology"),
        )
        self.store.record_item(item)
        self.store.commit()
        self.assertIn("https://example.com/a", self.store.seen_urls())

        self.assertFalse(self.store.delivered("2026-11-11", "wechat"))
        self.store.mark_delivery("2026-11-11", "wechat", "ok")
        self.assertTrue(self.store.delivered("2026-11-11", "wechat"))
        self.assertFalse(self.store.delivered("2026-11-11", "email"))

    def test_duplicate_url_is_idempotent(self):
        base = dict(
            item_id="it_0001",
            cluster_id="cl_0001",
            canonical_url="https://example.com/a",
            title_original="TETRA Россия",
            source="Media",
            source_kind="media",
            published_at="2026-04-10T10:00:00+03:00",
            first_seen="2026-04-10T10:00:00+03:00",
            facets=Facets(primary="technology"),
        )
        self.store.record_item(Item(**base))
        self.store.record_item(Item(**base))
        self.store.commit()
        row = self.store.conn.execute("SELECT seen_count FROM items").fetchone()
        self.assertEqual(row["seen_count"], 2)

    def test_week_items_keep_report_details(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.store.record_item(
            Item(
                item_id="it_0002",
                cluster_id="cl_0002",
                canonical_url="https://example.com/week",
                title_original="Росэл запустил TETRA",
                source="Yandex News",
                source_kind="aggregator",
                published_at=now,
                first_seen=now,
                snippet="Россия, базовые станции",
                facets=Facets(primary="technology"),
            )
        )
        self.store.commit()
        rows = self.store.week_items(days=7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title_original"], "Росэл запустил TETRA")
        self.assertEqual(rows[0]["source"], "Yandex News")


if __name__ == "__main__":
    unittest.main()
