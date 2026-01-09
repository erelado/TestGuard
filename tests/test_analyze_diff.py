import unittest

from testguard.analyze import RunMetrics, diff_metrics


class TestAnalyzeDiff(unittest.TestCase):
    def test_diff_classifies_memory_increase_as_warn(self) -> None:
        current = RunMetrics(duration_s=10.0, peak_rss_bytes=1150, total_read_bytes=None, total_write_bytes=None, peak_write_rate_bytes_s=None)
        baseline = RunMetrics(duration_s=10.0, peak_rss_bytes=1000, total_read_bytes=None, total_write_bytes=None, peak_write_rate_bytes_s=None)

        diff = diff_metrics(current, baseline, baseline_run_id="base")
        self.assertIn(diff.classification, ["WARN", "REGRESSION"])
        self.assertTrue(any(r["area"] == "memory" for r in diff.recommendations))
