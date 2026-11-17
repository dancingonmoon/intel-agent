import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intel.contracts import SourceHealth
from intel.learning import add_sources, disable_sources, load_overlay, overlay_path, source_metrics
from intel.store import Store


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "intel.db", "t")
        self.addCleanup(self.store.close)

    def test_metrics_use_health_selection_and_feedback(self):
        for run in ("20260101-t", "20260102-t", "20260103-t"):
            self.store.record_health(
                run,
                [
                    SourceHealth("good-source", ok=True, count=10),
                    SourceHealth("dead-source", ok=False, count=0, error="timeout"),
                ],
            )
        self.store.record_selection(
            "2026-01-03",
            [
                {"item_id": "i1", "source": "Publisher", "found_by": ["good-source"]},
                {"item_id": "i2", "source": "Publisher", "found_by": ["good-source"]},
            ],
        )
        self.store.record_feedback("good-source", "good")
        metrics = {row.source: row for row in source_metrics(self.store, "t", runs=3, days=3650)}
        self.assertEqual(metrics["good-source"].selected, 2)
        self.assertEqual(metrics["good-source"].good, 1)
        self.assertEqual(metrics["good-source"].verdict, "expand")
        self.assertEqual(metrics["dead-source"].verdict, "remove")

    def test_overlay_add_and_disable(self):
        with patch("intel.learning.TOPICS_DIR", Path(self.tmp.name)):
            add_sources("t", rss_urls=["https://example.com/rss"], channels=["chan"])
            disable_sources("t", ["dead-source"])
            data = load_overlay("t")
            self.assertEqual(data["add"]["rss"], ["https://example.com/rss"])
            self.assertEqual(data["add"]["telegram_channels"], ["chan"])
            self.assertEqual(data["disable"]["sources"], ["dead-source"])
            self.assertTrue(overlay_path("t").exists())


if __name__ == "__main__":
    unittest.main()
