from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Callable

from testguard.logger import ContextLoggerAdapter, get_named_logger
from testguard.monitor.collectors.base import CollectorAdapter, Target
from testguard.monitor.ring_buffer import RingBuffer


@dataclass(frozen=True, kw_only=True)
class Sample:
    ts_monotonic: float
    ts_wall_epoch: float
    fragments: Dict[str, float]


@dataclass(frozen=True, kw_only=True)
class MonitorConfig:
    sample_interval_seconds: float = 0.5
    max_consecutive_failures: int = 3
    ring_buffer_seconds: float = 30.0


class MonitorLoop:
    """
    Sampling loop for resource monitoring

    This module defines a small "monitor loop" that periodically polls one or more collectors
    (for example procfs, cgroup, PSI, GPU) for metrics about a target process (or process group).
    Each polling cycle merges the collectors' outputs into a single Sample with both monotonic time
    (for correct deltas and rates) and wall-clock time (for timestamps in reports)

    Samples are written into a fixed-size in-memory RingBuffer, so the rest of the system can inspect a recent window
    of metrics without keeping unbounded history in RAM

    The loop is designed to be resilient: if a collector fails, it logs a warning and keeps going, aborting only
    after too many consecutive failures (to avoid endless noisy loops)
    """

    def __init__(
            self,
            *,
            collectors: Sequence[CollectorAdapter],
            target: Target,
            config: MonitorConfig,
            logger: Optional[ContextLoggerAdapter] = None,
    ) -> None:
        self._collectors = list(collectors)
        self._target = target
        self._config = config

        ring_capacity = max(1, int(config.ring_buffer_seconds / config.sample_interval_seconds))
        self._ring_buffer = RingBuffer[Sample](capacity=ring_capacity)

        self._logger = logger or get_named_logger(__name__)

    @property
    def ring_buffer(self) -> RingBuffer[Sample]:
        return self._ring_buffer

    def tick_once(self) -> Optional[Sample]:
        """
        Perform one sampling tick across all collectors

        Each collector is called in sequence to gather a fragment of resource metrics for the target process or
        process group. Individual collector failures are logged but do not abort the tick. The resulting fragments are
        merged into a single dict (first writer wins on key collisions) and appended to the ring buffer

        Merge rules:
        - Each collector contributes a dict of numeric metrics
        - First writer wins for the "plain" key
        - If another collector emits the same key, the later value is preserved
          under a namespaced key: "<collector>.<key>" (and "<collector>.<key>#2" if needed)
        - Non-numeric values are ignored
        - One collector failing does not prevent sampling from others
        """
        sample_monotonic_seconds = time.monotonic()
        sample_wall_epoch_seconds = time.time()

        fragments: Dict[str, float] = {}

        def _insert_metric(*, collector_label: str, metric_key: str, metric_value: float) -> None:
            # Keep the original key if unused.
            if metric_key not in fragments:
                fragments[metric_key] = metric_value
                return

            # Collision: preserve the later value under a namespaced key.
            base_namespaced_key = f"{collector_label}.{metric_key}"
            namespaced_key = base_namespaced_key
            suffix_index = 2
            while namespaced_key in fragments:
                namespaced_key = f"{base_namespaced_key}#{suffix_index}"
                suffix_index += 1

            fragments[namespaced_key] = metric_value
            self._logger.debug(
                "metric key collision for %s, stored under %s",
                metric_key,
                namespaced_key,
            )

        for collector in self._collectors:
            collector_label = collector.__class__.__name__.lower()

            try:
                fragment = collector.sample(self._target)
            except Exception as exc:
                self._logger.warning("collector %s failed: %s", collector_label, exc)
                continue

            if not fragment:
                continue

            for metric_key, metric_value in fragment.items():
                if isinstance(metric_value, (int, float)):
                    _insert_metric(
                        collector_label=collector_label,
                        metric_key=str(metric_key),
                        metric_value=float(metric_value),
                    )
                    continue

                self._logger.debug(
                    "non-numeric metric ignored, collector=%s key=%s value_type=%s",
                    collector_label,
                    metric_key,
                    type(metric_value).__name__,
                )

        sample = Sample(
            ts_monotonic=sample_monotonic_seconds,
            ts_wall_epoch=sample_wall_epoch_seconds,
            fragments=fragments,
        )
        self._ring_buffer.append(sample)
        return sample


    def run_until(
        self,
        *,
        should_stop_callback: Callable[[], bool],
    ) -> None:
        """
        Run the monitor loop until the caller asks to stop

        Behavior:
        - Samples on a fixed cadence using monotonic time
        - If sampling falls behind (collector slowness, scheduler delay), it skips missed ticks and resynchronizes to
          "now + interval" to avoid a busy catch-up loop
        - Stops after too many consecutive failures to prevent endless noisy loops
        """
        sample_interval_seconds = float(self._config.sample_interval_seconds)
        assert sample_interval_seconds > 0.0

        max_consecutive_failures = int(self._config.max_consecutive_failures)
        assert max_consecutive_failures >= 1

        consecutive_failures = 0

        next_tick_monotonic = time.monotonic()

        while True:
            if should_stop_callback():
                return

            now_monotonic = time.monotonic()
            sleep_seconds = next_tick_monotonic - now_monotonic
            if sleep_seconds > 0.0:
                time.sleep(sleep_seconds)

                if should_stop_callback():
                    return
            else:
                # We are behind schedule. Avoid spinning by resyncing.
                next_tick_monotonic = now_monotonic

            try:
                self.tick_once()
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                self._logger.warning(
                    "collector tick failed (%d/%d): %s",
                    consecutive_failures,
                    max_consecutive_failures,
                    exc,
                )
                if consecutive_failures >= max_consecutive_failures:
                    self._logger.error("monitor aborting after repeated collector failures")
                    return

            next_tick_monotonic += sample_interval_seconds

            # If we are far behind (for example, a collector was slow), skip missed ticks.
            now_monotonic = time.monotonic()
            if next_tick_monotonic < now_monotonic:
                next_tick_monotonic = now_monotonic + sample_interval_seconds

