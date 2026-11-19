import unittest

from intel.cli import _week_row_relevant, _week_rows
from intel.config import load_topic


class CliWiringTests(unittest.TestCase):
    def test_deliver_is_imported(self):
        from intel import cli

        self.assertTrue(callable(cli.deliver))


class WeekRowsTests(unittest.TestCase):
    def test_prefers_fresh_items(self):
        rows = [{"canonical_url": "a"}, {"canonical_url": "b"}]
        self.assertEqual(_week_rows(rows, {"a"}), [rows[1]])

    def test_keeps_today_items_when_they_are_the_only_background(self):
        rows = [{"canonical_url": "a"}, {"canonical_url": "b"}]
        self.assertEqual(_week_rows(rows, {"a", "b"}), rows)


class WeekRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.topic = load_topic("ru-tetra")

    def test_old_false_positive_is_dropped(self):
        row = {
            "canonical_url": "https://example.com/micro",
            "title_original": "В контур российской мегакорпорации могут включить предприятие",
            "source": "CNews",
            "source_kind": "media",
            "published_at": "2026-11-11",
            "snippet": "отечественной электроники, радиосвязь",
        }
        self.assertFalse(_week_row_relevant(row, self.topic))

    def test_real_pmr_item_is_kept(self):
        row = {
            "canonical_url": "https://example.com/profcomm",
            "title_original": "«Информтехника» примет участие в XIII Международном форуме ProfComm 2026",
            "source": "Yandex News",
            "source_kind": "aggregator",
            "published_at": "2026-11-09",
            "snippet": "Россия, профессиональная мобильная радиосвязь",
        }
        self.assertTrue(_week_row_relevant(row, self.topic))


if __name__ == "__main__":
    unittest.main()
