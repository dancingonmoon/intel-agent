import json
import unittest
from pathlib import Path

from intel.config import load_topic
from intel.contracts import Digest, Report
from intel.render import build_fallback_report, render_markdown

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.digest = Digest.from_dict(json.loads((FIXTURES / "digest.sample.json").read_text(encoding="utf-8")))
        self.report = Report.from_dict(json.loads((FIXTURES / "report.sample.json").read_text(encoding="utf-8")))
        self.topic = load_topic("ru-tetra")

    def test_renders_citations_from_digest(self):
        text = render_markdown(self.digest, self.report, self.topic)
        self.assertIn("俄罗斯专网通信", text)
        self.assertIn("https://example.com/fixture/rosel-base-stations", text)
        self.assertIn("Rosel 量产升级版 TETRA 基站", text)
        self.assertIn("另有 3 家报道", text)
        self.assertIn("降级来源：x", text)

    def test_fallback_report_has_banner_and_no_translation(self):
        fallback = build_fallback_report(self.digest, self.topic, reason="llm-unavailable")
        text = render_markdown(self.digest, fallback, self.topic)
        self.assertIn("自动摘要不可用", text)
        self.assertIn("V Rossii zapushcheno proizvodstvo", text)

    def test_week_review_section(self):
        self.report.week_review = ["趋势一", "趋势二"]
        text = render_markdown(self.digest, self.report, self.topic)
        self.assertIn("## 二、本周回顾", text)
        self.assertIn("- 趋势一", text)

    def test_rolling_window_uses_week_labels(self):
        self.digest.window.start = "2026-11-04T00:00:00+00:00"
        self.digest.window.end = "2026-11-11T00:00:00+00:00"
        text = render_markdown(self.digest, self.report, self.topic)
        self.assertIn("## 一、本周要点", text)

    def test_russian_titles_use_english_translation(self):
        self.report.title_en = {
            "it_0001": "Rosel launches upgraded TETRA base stations for energy and security"
        }
        text = render_markdown(self.digest, self.report, self.topic)
        self.assertIn("Rosel launches upgraded TETRA base stations", text)
        self.assertIn("原文（英译）", text)
        self.assertNotIn("«Росэл» запустил серийное производство", text)

    def test_final_output_has_no_cyrillic(self):
        self.report.sections["industry"][0].summary_zh = "РТК-Сервис 总经理说，国产化仍需验证。"
        text = render_markdown(self.digest, self.report, self.topic)
        self.assertNotIn("РТК", text)
        self.assertIn("RTK-Servis", text)


if __name__ == "__main__":
    unittest.main()
