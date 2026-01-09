from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Thresholds:
    warn_rss_bytes: int
    max_rss_bytes: int
    max_runtime_s: Optional[float] = None

    disk_write_rate_bytes_s: Optional[float] = None
    disk_write_sustain_s: float = 3.0


@dataclass(frozen=True)
class GovernorDecision:
    level: str  # "NONE" | "WARN" | "PANIC"
    policy_id: Optional[str]
    message: Optional[str]


class SustainedCondition:
    def __init__(self, required_seconds: float) -> None:
        self._required_seconds = required_seconds
        self._start_ts: Optional[float] = None

    def update(self, *, is_true: bool, ts: float) -> bool:
        if not is_true:
            self._start_ts = None
            return False

        if self._start_ts is None:
            self._start_ts = ts
            return False

        return (ts - self._start_ts) >= self._required_seconds


class Governor:
    def __init__(self, thresholds: Thresholds) -> None:
        self._thresholds = thresholds
        self._warned_rss = False
        self._last_write_bytes: Optional[float] = None
        self._last_ts: Optional[float] = None
        self._write_sustained = SustainedCondition(required_seconds=thresholds.disk_write_sustain_s)

    def evaluate(self, *, sample_ts: float, fragments: dict, started_monotonic: float) -> GovernorDecision:
        rss_bytes = fragments.get("rss_bytes")
        if isinstance(rss_bytes, (int, float)):
            if (not self._warned_rss) and rss_bytes >= self._thresholds.warn_rss_bytes:
                self._warned_rss = True
                return GovernorDecision(
                    level="WARN",
                    policy_id="memory.rss.warn",
                    message=f"RSS is high: {int(rss_bytes)} bytes (warn threshold {self._thresholds.warn_rss_bytes})",
                )

            if rss_bytes >= self._thresholds.max_rss_bytes:
                return GovernorDecision(
                    level="PANIC",
                    policy_id="memory.rss.panic",
                    message=f"RSS exceeded max: {int(rss_bytes)} bytes (max {self._thresholds.max_rss_bytes})",
                )

        if self._thresholds.max_runtime_s is not None:
            elapsed_s = sample_ts - started_monotonic
            if elapsed_s >= self._thresholds.max_runtime_s:
                return GovernorDecision(
                    level="PANIC",
                    policy_id="runtime.timeout.panic",
                    message=f"Runtime exceeded max: {elapsed_s:.2f}s (max {self._thresholds.max_runtime_s:.2f}s)",
                )

        if self._thresholds.disk_write_rate_bytes_s is not None:
            write_total = fragments.get("io_write_bytes_total")
            if isinstance(write_total, (int, float)):
                if self._last_write_bytes is not None and self._last_ts is not None:
                    dt = sample_ts - self._last_ts
                    if dt > 0:
                        rate = (write_total - self._last_write_bytes) / dt
                        sustained = self._write_sustained.update(
                            is_true=rate >= self._thresholds.disk_write_rate_bytes_s,
                            ts=sample_ts,
                        )
                        if sustained:
                            return GovernorDecision(
                                level="PANIC",
                                policy_id="disk.write_rate.panic",
                                message=f"Disk write rate sustained high: {rate:.1f} B/s",
                            )

                self._last_write_bytes = float(write_total)
                self._last_ts = float(sample_ts)

        return GovernorDecision(level="NONE", policy_id=None, message=None)
