import unittest

from testguard.governor import Governor, Thresholds


class TestGovernorCgroupMemoryLimit(unittest.TestCase):
    def test_panics_near_cgroup_limit(self) -> None:
        thresholds = Thresholds(
            warn_rss_bytes=0,
            max_rss_bytes=0,
            max_runtime_s=None,
            disk_write_rate_bytes_s=None,
            disk_write_sustain_s=3.0,
        )
        governor = Governor(thresholds)

        decision = governor.evaluate(
            sample_ts=10.0,
            started_monotonic=0.0,
            fragments={
                "cgroup_memory_current_bytes": 95,
                "cgroup_memory_max_bytes": 100,
            },
        )

        self.assertEqual(decision.level, "PANIC")
        self.assertEqual(decision.policy_id, "memory.cgroup.near_limit.panic")
