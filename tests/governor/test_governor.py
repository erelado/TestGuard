import unittest
from testguard.governor import Governor, Thresholds


class TestGovernor(unittest.TestCase):
    def test_memory_warn_then_panic(self) -> None:
        thresholds = Thresholds(warn_rss_bytes=100, max_rss_bytes=200, max_runtime_s=None)
        governor = Governor(thresholds)

        d1 = governor.evaluate(sample_ts=0.0, fragments={"rss_bytes": 120}, started_monotonic=0.0)
        self.assertEqual(d1.level, "WARN")

        d2 = governor.evaluate(sample_ts=0.5, fragments={"rss_bytes": 130}, started_monotonic=0.0)
        self.assertEqual(d2.level, "NONE")  # warn only once

        d3 = governor.evaluate(sample_ts=1.0, fragments={"rss_bytes": 250}, started_monotonic=0.0)
        self.assertEqual(d3.level, "PANIC")

    def test_timeout_panic(self) -> None:
        thresholds = Thresholds(warn_rss_bytes=10**12, max_rss_bytes=10**12, max_runtime_s=2.0)
        governor = Governor(thresholds)

        d1 = governor.evaluate(sample_ts=1.0, fragments={}, started_monotonic=0.0)
        self.assertEqual(d1.level, "NONE")

        d2 = governor.evaluate(sample_ts=2.1, fragments={}, started_monotonic=0.0)
        self.assertEqual(d2.level, "PANIC")
