import json
import unittest
from pathlib import Path

from intel.contracts import Digest
from intel.deep_research import _fallback, _resolve_citations, _slug

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class DeepResearchTests(unittest.TestCase):
    def setUp(self):
        self.digest = Digest.from_dict(json.loads((FIXTURES / "digest.sample.json").read_text(encoding="utf-8")))

    def test_citations_resolve_to_digest_links(self):
        text = "结论一 [[it_0001]]，未知引用 [[it_9999]]。"
        rendered = _resolve_citations(text, self.digest)
        self.assertIn("https://example.com/fixture/rosel-base-stations", rendered)
        self.assertIn("[[it_9999]]", rendered)

    def test_fallback_evidence_is_resolvable(self):
        text = _fallback("俄罗斯 TETRA 国产化", self.digest, "provider-none")
        rendered = _resolve_citations(text, self.digest)
        self.assertNotIn("[[it_", rendered)
        self.assertIn("https://example.com/fixture/", rendered)
        self.assertIn("证据", rendered)

    def test_slug_is_filesystem_safe(self):
        slug = _slug("俄罗斯 TETRA 国产化进展与主要玩家")
        self.assertNotIn("/", slug)
        self.assertTrue(slug)


if __name__ == "__main__":
    unittest.main()
