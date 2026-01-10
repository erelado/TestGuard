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
        thresholds = Thresholds(warn_rss_bytes=10 ** 12, max_rss_bytes=10 ** 12, max_runtime_s=2.0)
        governor = Governor(thresholds)

        d1 = governor.evaluate(sample_ts=1.0, fragments={}, started_monotonic=0.0)
        self.assertEqual(d1.level, "NONE")

        d2 = governor.evaluate(sample_ts=2.1, fragments={}, started_monotonic=0.0)
        self.assertEqual(d2.level, "PANIC")


class TestGovernorDiskWrite(unittest.TestCase):
    def test_disk_write_sustained_triggers_panic(self) -> None:
        thresholds = Thresholds(
            warn_rss_bytes=0,
            max_rss_bytes=0,
            max_runtime_s=None,
            disk_write_rate_bytes_s=100.0,  # 100 B/s
            disk_write_sustain_s=2.0,  # sustain for 2 seconds
        )
        governor = Governor(thresholds)

        started_monotonic = 0.0

        # Simulate sustained write rate above limit.
        # Depending on the implementation, the sustain window may start on the first over-limit interval,
        # so panic can occur on the next interval after the full sustain duration has elapsed.
        decision1 = governor.evaluate(
            sample_ts=1.0,
            fragments={"io_write_bytes_total": 0},
            started_monotonic=started_monotonic,
        )
        self.assertEqual(decision1.level, "NONE")

        decision2 = governor.evaluate(
            sample_ts=2.0,
            fragments={"io_write_bytes_total": 200},
            started_monotonic=started_monotonic,
        )
        self.assertIn(decision2.level, {"NONE", "WARN"})

        decision3 = governor.evaluate(
            sample_ts=3.0,
            fragments={"io_write_bytes_total": 400},
            started_monotonic=started_monotonic,
        )
        self.assertIn(decision3.level, {"NONE", "WARN"})

        # One more interval keeps the rate high long enough to satisfy sustain duration.
        decision4 = governor.evaluate(
            sample_ts=4.0,
            fragments={"io_write_bytes_total": 600},
            started_monotonic=started_monotonic,
        )
        self.assertEqual(decision4.level, "PANIC")
        self.assertEqual(decision4.policy_id, "disk.write_rate.panic")
