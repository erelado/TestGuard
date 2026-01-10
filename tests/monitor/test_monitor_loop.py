import time
import unittest

from testguard.monitor.monitor_loop import MonitorLoop, MonitorConfig, Sample


def make_collector(name: str, fragment: dict, fail: bool = False):
    """Return a dummy collector instance with a predictable class name."""

    class NamedCollector:
        def sample(self, target):
            if fail:
                raise RuntimeError(f"{name} failed")
            return fragment

    NamedCollector.__name__ = name
    return NamedCollector()


class DummyTarget:
    pass


class TestMonitorLoop(unittest.TestCase):
    def test_tick_once_merges_collectors(self):
        c1 = make_collector("AlphaCollector", {"a": 1, "b": 2})
        c2 = make_collector("BetaCollector", {"c": 3})
        monitor = MonitorLoop(
            collectors=[c1, c2],
            target=DummyTarget(),
            config=MonitorConfig(sample_interval_s=0.1),
        )

        sample = monitor.tick_once()
        self.assertIsInstance(sample, Sample)
        self.assertIn("a", sample.fragments)
        self.assertIn("c", sample.fragments)
        self.assertEqual(len(sample.fragments), 3)  # a, b, c

    def test_tick_once_skips_non_numeric_and_handles_collision(self):
        c1 = make_collector("AlphaCollector", {"x": 1})
        c2 = make_collector("BetaCollector", {"x": 2, "y": "not_number"})
        monitor = MonitorLoop(
            collectors=[c1, c2],
            target=DummyTarget(),
            config=MonitorConfig(),
        )

        sample = monitor.tick_once()
        # Collision: both collectors report 'x' → BetaCollector.x should exist
        self.assertIn("x", sample.fragments)
        self.assertIn("betacollector.x", sample.fragments)
        self.assertNotIn("y", sample.fragments)
        self.assertEqual(sample.fragments["x"], 1.0)
        self.assertEqual(sample.fragments["betacollector.x"], 2.0)

    def test_tick_once_handles_collector_failure(self):
        c1 = make_collector("AlphaCollector", {"ok": 1})
        c2 = make_collector("BrokenCollector", {}, fail=True)
        monitor = MonitorLoop(
            collectors=[c1, c2],
            target=DummyTarget(),
            config=MonitorConfig(),
        )

        sample = monitor.tick_once()
        self.assertIn("ok", sample.fragments)
        self.assertEqual(sample.fragments["ok"], 1.0)

    def test_run_until_stops_when_callback_returns_true(self):
        monitor = MonitorLoop(
            collectors=[],
            target=DummyTarget(),
            config=MonitorConfig(sample_interval_s=0.01),
        )

        calls = []

        def should_stop_callback():
            calls.append(time.time())
            return len(calls) > 3

        monitor.run_until(should_stop_callback=should_stop_callback)
        self.assertGreaterEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
