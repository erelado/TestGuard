from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from testguard.logger import ContextLoggerAdapter, get_logger, get_named_logger
from testguard.monitor.collectors.base import CollectorAdapter, Target
from testguard.monitor.ring_buffer import RingBuffer


@dataclass(frozen=True)
class Sample:
    ts_monotonic: float
    ts_wall_epoch: float
    fragments: Dict[str, float]


@dataclass(frozen=True)
class MonitorConfig:
    sample_interval_s: float = 0.5
    max_consecutive_failures: int = 3
    ring_buffer_seconds: float = 30.0


class MonitorLoop:
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

        ring_capacity = max(1, int(config.ring_buffer_seconds / config.sample_interval_s))
        self._ring_buffer = RingBuffer[Sample](capacity=ring_capacity)

        self._logger = logger or get_named_logger(__name__)

    @property
    def ring_buffer(self) -> RingBuffer[Sample]:
        return self._ring_buffer

    def tick_once(self) -> Optional[Sample]:
        fragments: Dict[str, float] = {}
        for collector in self._collectors:
            fragment = collector.sample(self._target)
            fragments.update(fragment.values)

        sample = Sample(
            ts_monotonic=time.monotonic(),
            ts_wall_epoch=time.time(),
            fragments=fragments,
        )
        self._ring_buffer.append(sample)
        return sample

    def run_until(
            self,
            *,
            should_stop,
    ) -> None:
        consecutive_failures = 0
        next_tick = time.monotonic()

        while True:
            if should_stop():
                return

            now = time.monotonic()
            sleep_s = next_tick - now
            if sleep_s > 0:
                time.sleep(sleep_s)

            try:
                self.tick_once()
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                self._logger.warning(
                    "collector tick failed (%d/%d): %s",
                    consecutive_failures,
                    self._config.max_consecutive_failures,
                    exc,
                )
                if consecutive_failures >= self._config.max_consecutive_failures:
                    self._logger.error("monitor aborting after repeated collector failures")
                    return

            next_tick += self._config.sample_interval_s
