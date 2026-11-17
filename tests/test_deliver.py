import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intel.config import load_topic
from intel.contracts import Digest, Report
from intel.deliver import deliver

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _settings(tmp: str, primary: str) -> dict:
    return {
        "paths": {"reports_dir": tmp},
        "delivery": {
            "primary": primary,
            "wechat": {
                "enabled": True,
                "format": "text",
                "max_chars": 1200,
                "script": "/bin/true",
            },
            "email": {
                "enabled": False,
                "cli": "/bin/true",
                "to": "nobody@example.com",
                "subject_prefix": "[intel]",
            },
            "feishu": {"enabled": True, "as": "bot", "open_id": "ou_test"},
        },
    }


class DeliveryRoutingTests(unittest.TestCase):
    def setUp(self):
        self.digest = Digest.from_dict(json.loads((FIXTURES / "digest.sample.json").read_text(encoding="utf-8")))
        self.report = Report.from_dict(json.loads((FIXTURES / "report.sample.json").read_text(encoding="utf-8")))
        self.topic = load_topic("ru-tetra")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_feishu_primary_skips_wechat_on_success(self):
        settings = _settings(self.tmp.name, "feishu")
        with patch(
            "intel.deliver.send_feishu",
            return_value={"ok": True, "url": "https://example.com/doc", "output": "sent"},
        ), patch("intel.deliver.send_wechat") as wechat:
            results = deliver("# report\n", self.digest, self.report, self.topic, settings)
        self.assertTrue(results["feishu"]["ok"])
        self.assertTrue(results["wechat"]["skipped"])
        wechat.assert_not_called()

    def test_feishu_primary_falls_back_to_wechat(self):
        settings = _settings(self.tmp.name, "feishu")
        with patch(
            "intel.deliver.send_feishu",
            return_value={"ok": False, "error": "publish failed"},
        ), patch("intel.deliver.send_wechat", return_value=(True, "sent")) as wechat:
            results = deliver("# report\n", self.digest, self.report, self.topic, settings)
        self.assertFalse(results["feishu"]["ok"])
        self.assertTrue(results["feishu"]["fallback_to_wechat"])
        self.assertTrue(results["wechat"]["ok"])
        wechat.assert_called_once()

    def test_wechat_primary_sends_both_enabled_channels(self):
        settings = _settings(self.tmp.name, "wechat")
        with patch(
            "intel.deliver.send_feishu",
            return_value={"ok": True, "url": "https://example.com/doc", "output": "sent"},
        ) as feishu, patch("intel.deliver.send_wechat", return_value=(True, "sent")) as wechat:
            results = deliver("# report\n", self.digest, self.report, self.topic, settings)
        self.assertTrue(results["wechat"]["ok"])
        self.assertTrue(results["feishu"]["ok"])
        wechat.assert_called_once()
        feishu.assert_called_once()


if __name__ == "__main__":
    unittest.main()
