import time
import unittest

from testguard.monitor.collectors.base import Target
from testguard.monitor.monitor_loop import MonitorLoop, MonitorConfig, Sample


class SilentTestLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.debugs: list[str] = []

    def debug(self, msg: str, *args, **kwargs) -> None:
        if args:
            msg = msg % args
        self.debugs.append(msg)

    def info(self, msg: str, *args, **kwargs) -> None:
        return

    def warning(self, msg: str, *args, **kwargs) -> None:
        if args:
            msg = msg % args
        self.warnings.append(msg)

    def error(self, msg: str, *args, **kwargs) -> None:
        if args:
            msg = msg % args
        self.errors.append(msg)


def make_collector(class_name: str, fragment: dict, *, fail: bool = False):
    """Return a dummy collector instance with a stable class name."""

    def sample(self, target):
        if fail:
            raise RuntimeError(f"{class_name} failed")
        return fragment

    collector_type = type(class_name, (), {"sample": sample})
    return collector_type()


class DummyTarget(Target):
    pass


class TestMonitorLoop(unittest.TestCase):
    def test_tick_once_merges_collectors(self):
        logger = SilentTestLogger()
        c1 = make_collector("AlphaCollector", {"a": 1, "b": 2})
        c2 = make_collector("BetaCollector", {"c": 3})
        monitor = MonitorLoop(
            collectors=[c1, c2],
            target=DummyTarget(root_pid=12345),
            config=MonitorConfig(sample_interval_s=0.1),
            logger=logger,
        )

        sample = monitor.tick_once()
        self.assertIsInstance(sample, Sample)
        self.assertEqual(sample.fragments["a"], 1.0)
        self.assertEqual(sample.fragments["b"], 2.0)
        self.assertEqual(sample.fragments["c"], 3.0)

    def test_tick_once_skips_non_numeric_and_handles_collision(self):
        logger = SilentTestLogger()
        c1 = make_collector("AlphaCollector", {"x": 1})
        c2 = make_collector("BetaCollector", {"x": 2, "y": "not_number"})
        monitor = MonitorLoop(
            collectors=[c1, c2],
            target=DummyTarget(root_pid=123),
            config=MonitorConfig(),
            logger=logger,
        )

        sample = monitor.tick_once()
        self.assertIn("x", sample.fragments)
        self.assertIn("betacollector.x", sample.fragments)
        self.assertNotIn("y", sample.fragments)

        self.assertEqual(sample.fragments["x"], 1.0)
        self.assertEqual(sample.fragments["betacollector.x"], 2.0)

    def test_tick_once_handles_collector_failure(self):
        logger = SilentTestLogger()
        c1 = make_collector("AlphaCollector", {"ok": 1})
        c2 = make_collector("BrokenCollector", {}, fail=True)
        monitor = MonitorLoop(
            collectors=[c1, c2],
            target=DummyTarget(root_pid=999),
            config=MonitorConfig(),
            logger=logger,
        )

        sample = monitor.tick_once()
        self.assertEqual(sample.fragments["ok"], 1.0)

        # Optional: verify we logged the failure, without printing to console
        self.assertTrue(any("collector brokencollector failed:" in msg for msg in logger.warnings))

    def test_run_until_stops_when_callback_returns_true(self):
        logger = SilentTestLogger()
        monitor = MonitorLoop(
            collectors=[],
            target=DummyTarget(root_pid=42),
            config=MonitorConfig(sample_interval_s=0.001),
            logger=logger,
        )

        calls: list[float] = []

        def should_stop_callback() -> bool:
            calls.append(time.time())
            return len(calls) > 3

        monitor.run_until(should_stop_callback=should_stop_callback)
        self.assertGreaterEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
