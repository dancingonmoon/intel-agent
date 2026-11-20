import io
import unittest

from intel.progress import Progress


class ProgressTests(unittest.TestCase):
    def test_stage_and_step_are_visible(self):
        stream = io.StringIO()
        progress = Progress(stream=stream)
        progress.stage("采集")
        progress.step(2, 4, "RSS: example")
        progress.finish("done")
        text = stream.getvalue()
        self.assertIn("采集", text)
        self.assertIn("[##", text)
        self.assertIn("2/4", text)
        self.assertIn("done", text)

    def test_elapsed_is_human_readable(self):
        progress = Progress(stream=io.StringIO())
        self.assertRegex(progress.elapsed, r"^\d+[smh]")


if __name__ == "__main__":
    unittest.main()
