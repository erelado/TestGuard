from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, kw_only=True)
class Thresholds:
    warn_rss_bytes: int
    max_rss_bytes: int
    max_runtime_s: Optional[float] = None

    disk_write_rate_bytes_s: Optional[float] = None
    disk_write_sustain_s: float = 3.0


@dataclass(frozen=True, kw_only=True)
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


def _pick_observed_memory_bytes(fragments: dict) -> Optional[int]:
    # Prefer cgroup v2 memory.current when available, otherwise fall back to RSS.
    for key in ("cgroup_memory_current_bytes", "rss_bytes"):
        value = fragments.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None


class Governor:
    def __init__(self, thresholds: Thresholds) -> None:
        self._thresholds = thresholds

        self._warned_memory = False

        self._last_write_total_bytes: Optional[float] = None
        self._last_write_sample_ts: Optional[float] = None
        self._write_over_limit_since_ts: Optional[float] = None

    def evaluate(self, *, sample_ts: float, fragments: dict, started_monotonic: float) -> GovernorDecision:
        observed_memory_bytes = _pick_observed_memory_bytes(fragments)

        if observed_memory_bytes is not None:
            warn_bytes = int(self._thresholds.warn_rss_bytes)
            max_bytes = int(self._thresholds.max_rss_bytes)

            if warn_bytes > 0 and (not self._warned_memory) and observed_memory_bytes >= warn_bytes:
                self._warned_memory = True
                return GovernorDecision(
                    level="WARN",
                    policy_id="memory.usage.warn",
                    message=(
                        f"Memory usage is high: {observed_memory_bytes} bytes "
                        f"(warn threshold {warn_bytes})"
                    ),
                )

            if max_bytes > 0 and observed_memory_bytes >= max_bytes:
                return GovernorDecision(
                    level="PANIC",
                    policy_id="memory.usage.panic",
                    message=(
                        f"Memory usage exceeded max: {observed_memory_bytes} bytes "
                        f"(max {max_bytes})"
                    ),
                )

        # Warn / panic on configured memory thresholds (0 disables).
        if observed_memory_bytes is not None:
            if self._thresholds.warn_rss_bytes > 0:
                if (not self._warned_memory) and observed_memory_bytes >= float(self._thresholds.warn_rss_bytes):
                    self._warned_memory = True
                    return GovernorDecision(
                        level="WARN",
                        policy_id="memory.usage.warn",
                        message=(
                            f"Memory usage is high: {int(observed_memory_bytes)} bytes "
                            f"(warn threshold {self._thresholds.warn_rss_bytes})"
                        ),
                    )

            if self._thresholds.max_rss_bytes > 0:
                if observed_memory_bytes >= float(self._thresholds.max_rss_bytes):
                    return GovernorDecision(
                        level="PANIC",
                        policy_id="memory.usage.panic",
                        message=(
                            f"Memory usage exceeded max: {int(observed_memory_bytes)} bytes "
                            f"(max {self._thresholds.max_rss_bytes})"
                        ),
                    )

        # Panic near cgroup memory limit (only when max memory threshold is configured and both values exist).
        if self._thresholds.max_rss_bytes > 0:
            cgroup_current = fragments.get("cgroup_memory_current_bytes")
            cgroup_max = fragments.get("cgroup_memory_max_bytes")
            if isinstance(cgroup_current, (int, float)) \
                    and isinstance(cgroup_max, (int, float)) and float(cgroup_max) > 0:
                if float(cgroup_current) >= 0.95 * float(cgroup_max):
                    return GovernorDecision(
                        level="PANIC",
                        policy_id="memory.cgroup.near_limit.panic",
                        message=(
                            f"Memory near cgroup limit: {int(cgroup_current)} bytes "
                            f"(limit {int(cgroup_max)} bytes, threshold 95%)"
                        ),
                    )

        # Timeout panic
        if self._thresholds.max_runtime_s is not None:
            elapsed_s = sample_ts - started_monotonic
            if elapsed_s >= self._thresholds.max_runtime_s:
                return GovernorDecision(
                    level="PANIC",
                    policy_id="runtime.timeout.panic",
                    message=f"Runtime exceeded max: {elapsed_s:.2f}s (max {self._thresholds.max_runtime_s:.2f}s)",
                )

        # Disk write sustained panic
        write_rate_limit = self._thresholds.disk_write_rate_bytes_s
        if write_rate_limit is not None and write_rate_limit > 0:
            write_total = fragments.get("io_write_bytes_total")
            if isinstance(write_total, (int, float)):
                write_total_bytes = float(write_total)

                if self._last_write_total_bytes is not None and self._last_write_sample_ts is not None:
                    interval_end_ts = float(sample_ts)
                    interval_start_ts = float(self._last_write_sample_ts)

                    interval_seconds = interval_end_ts - interval_start_ts
                    written_bytes = write_total_bytes - float(self._last_write_total_bytes)

                    if interval_seconds > 0 and written_bytes >= 0:
                        write_rate_bytes_s = written_bytes / interval_seconds
                        is_over_limit = write_rate_bytes_s >= float(write_rate_limit)

                        if is_over_limit:
                            if self._write_over_limit_since_ts is None:
                                self._write_over_limit_since_ts = interval_start_ts

                            sustained_seconds = interval_end_ts - float(self._write_over_limit_since_ts)
                            if sustained_seconds >= float(self._thresholds.disk_write_sustain_s):
                                return GovernorDecision(
                                    level="PANIC",
                                    policy_id="disk.write_rate.panic",
                                    message=(
                                        f"Disk write rate sustained high: {write_rate_bytes_s:.1f} B/s "
                                        f"(limit {float(write_rate_limit):.1f} B/s, sustain {self._thresholds.disk_write_sustain_s:.1f}s)"
                                    ),
                                )
                        else:
                            self._write_over_limit_since_ts = None
                    else:
                        self._write_over_limit_since_ts = None

                self._last_write_total_bytes = write_total_bytes
                self._last_write_sample_ts = float(sample_ts)

        return GovernorDecision(level="NONE", policy_id=None, message=None)
