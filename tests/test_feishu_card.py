import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intel.contracts import Digest, Report
from intel.feishu import build_card, recall_previous, send_card

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FeishuCardTests(unittest.TestCase):
    def setUp(self):
        self.digest = Digest.from_dict(json.loads((FIXTURES / "digest.sample.json").read_text(encoding="utf-8")))
        self.report = Report.from_dict(json.loads((FIXTURES / "report.sample.json").read_text(encoding="utf-8")))
        # CI has no lark-cli; individual tests mock the command runner.
        patcher = patch("intel.feishu.available", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_card_has_counts_headlines_and_open_url_button(self):
        card = build_card(
            "俄罗斯 TETRA 每日情报 2026-11-11",
            "https://example.com/docx/token",
            self.digest,
            self.report,
            ["· 头条一（Source）", "· 头条二（Source）"],
        )
        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(card["header"]["template"], "blue")
        self.assertEqual(card["header"]["title"]["content"], "俄罗斯 TETRA 每日情报 2026-11-11")
        elements = card["body"]["elements"]
        self.assertEqual(elements[0]["tag"], "column_set")
        self.assertEqual(len(elements[0]["columns"]), 4)
        for column in elements[0]["columns"]:
            self.assertNotIn("corner_radius", column)  # rejected by the card API
        self.assertEqual(
            [col["elements"][0]["content"] for col in elements[0]["columns"]],
            [
                "## <font color='blue'>2</font>",
                "## <font color='blue'>1</font>",
                "## <font color='blue'>3</font>",
                "## <font color='blue'>1</font>",
            ],
        )
        self.assertIn("- 头条一（Source）", elements[1]["content"])
        button = elements[2]
        self.assertEqual(button["tag"], "button")
        self.assertEqual(button["type"], "primary_filled")
        self.assertEqual(button["behaviors"][0]["default_url"], "https://example.com/docx/token")

    def test_rolling_window_uses_week_label(self):
        self.digest.window.start = "2026-11-04T00:00:00+00:00"
        self.digest.window.end = "2026-11-11T00:00:00+00:00"
        card = build_card("标题", "https://example.com/docx/token", self.digest, self.report, [])
        self.assertIn("本周要点", card["body"]["elements"][1]["content"])

    def test_send_card_uses_interactive_message_type(self):
        with patch("intel.feishu._run", return_value=(True, {"ok": True})) as run:
            ok, _ = send_card(
                "https://example.com/docx/token",
                "标题",
                self.digest,
                self.report,
                ["· 头条"],
                open_id="ou_test",
            )
        self.assertTrue(ok)
        cmd = run.call_args.args[0]
        self.assertIn("interactive", cmd)
        content = json.loads(cmd[cmd.index("--content") + 1])
        self.assertEqual(content["schema"], "2.0")

    def test_send_card_recalls_previous_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "cards.json"
            state_path.write_text(json.dumps({"ru-tetra": ["om_old"]}), encoding="utf-8")
            with patch("intel.feishu.STATE_PATH", state_path), patch(
                "intel.feishu._run",
                side_effect=[
                    (True, {"ok": True, "data": {"message_id": "om_new"}}),
                    (True, {"ok": True}),
                ],
            ) as run:
                ok, output = send_card(
                    "https://example.com/docx/token",
                    "标题",
                    self.digest,
                    self.report,
                    ["· 头条"],
                    open_id="ou_test",
                    topic_id="ru-tetra",
                )
            self.assertTrue(ok)
            self.assertIn("recalled=1", output)
            self.assertEqual(run.call_count, 2)
            recall_cmd = run.call_args_list[1].args[0]
            self.assertIn("messages", recall_cmd)
            self.assertIn("--message-id", recall_cmd)
            self.assertIn("om_old", recall_cmd)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["ru-tetra"], ["om_new"])

    def test_send_card_falls_back_to_post(self):
        with patch(
            "intel.feishu._run",
            side_effect=[(False, "card rejected"), (True, {"ok": True})],
        ) as run:
            ok, _ = send_card(
                "https://example.com/docx/token",
                "标题",
                self.digest,
                self.report,
                ["· 头条"],
                open_id="ou_test",
            )
        self.assertTrue(ok)
        self.assertEqual(run.call_count, 2)
        fallback_cmd = run.call_args_list[1].args[0]
        self.assertIn("--markdown", fallback_cmd)

    def test_expired_card_is_dropped_from_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "cards.json"
            state_path.write_text(json.dumps({"ru-tetra": ["om_old"]}), encoding="utf-8")
            with patch("intel.feishu.STATE_PATH", state_path), patch(
                "intel.feishu._run",
                return_value=(False, {"error": {"code": 230009, "message": "Message has expired"}}),
            ):
                results = recall_previous("ru-tetra", "om_new")
            self.assertFalse(results[0]["ok"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["ru-tetra"], ["om_new"])


if __name__ == "__main__":
    unittest.main()
