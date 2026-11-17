import json
import unittest
from pathlib import Path

from intel.contracts import Digest, Report, validate_report_data

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.digest = Digest.from_dict(json.loads((FIXTURES / "digest.sample.json").read_text(encoding="utf-8")))
        self.report_data = json.loads((FIXTURES / "report.sample.json").read_text(encoding="utf-8"))

    def test_sample_report_valid(self):
        self.assertEqual(validate_report_data(self.report_data, self.digest), [])
        report = Report.from_dict(self.report_data)
        self.assertEqual(report.report_date, "2026-11-11")
        self.assertEqual(len(report.sections["technology"]), 3)

    def test_url_in_report_is_rejected(self):
        data = json.loads(json.dumps(self.report_data))
        data["sections"]["contracts"][0]["url"] = "https://evil.example/x"
        errors = validate_report_data(data, self.digest)
        self.assertTrue(any("forbidden fields" in e for e in errors), errors)

    def test_unknown_item_id_is_rejected(self):
        data = json.loads(json.dumps(self.report_data))
        data["sections"]["industry"].append({"item_id": "it_9999", "title_zh": "x", "summary_zh": "y"})
        errors = validate_report_data(data, self.digest)
        self.assertTrue(any("unknown item_id: it_9999" in e for e in errors), errors)

    def test_no_news_facet_must_be_empty(self):
        data = json.loads(json.dumps(self.report_data))
        data["coverage"]["no_news_facets"] = ["contracts"]
        errors = validate_report_data(data, self.digest)
        self.assertTrue(any("no news for contracts" in e for e in errors), errors)

    def test_week_review_is_optional_but_typed(self):
        data = json.loads(json.dumps(self.report_data))
        data["week_review"] = ["本周趋势一", "本周趋势二"]
        self.assertEqual(validate_report_data(data, self.digest), [])
        data["week_review"] = [123]
        errors = validate_report_data(data, self.digest)
        self.assertTrue(any("week_review[0]" in e for e in errors), errors)

    def test_title_en_map_is_validated_and_parsed(self):
        data = json.loads(json.dumps(self.report_data))
        data["title_en"] = {"it_0001": "Rosel launches upgraded TETRA base stations"}
        self.assertEqual(validate_report_data(data, self.digest), [])
        self.assertEqual(
            Report.from_dict(data).title_en,
            {"it_0001": "Rosel launches upgraded TETRA base stations"},
        )
        data["title_en"] = {"it_9999": "unknown"}
        errors = validate_report_data(data, self.digest)
        self.assertTrue(any("title_en references unknown item_id" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
