import unittest

from testguard.governor import Governor, Thresholds



from testguard.governor import Governor, Thresholds


class TestGovernorCgroupMemoryLimit(unittest.TestCase):
    def test_panics_near_cgroup_limit(self) -> None:
        thresholds = Thresholds(
            warn_rss_bytes=0,
            max_rss_bytes=0,
            max_runtime_s=None,
        )
        governor = Governor(thresholds)

        decision = governor.evaluate(
            sample_ts=0.0,
            started_monotonic=0.0,
            fragments={
                "cgroup_memory_current_bytes": 950,
                "cgroup_memory_max_bytes": 1000,
            },
        )

        self.assertEqual(decision.level, "PANIC")
        # Updated to match current implementation.
        self.assertEqual(decision.policy_id, "memory.limit.near.panic")
