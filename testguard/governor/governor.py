from __future__ import annotations

from typing import Mapping, Optional

from testguard.governor.decision import GovernorDecision
from testguard.governor.sustained import SustainedCondition
from testguard.governor.thresholds import Thresholds


_CGROUP_NEAR_LIMIT_RATIO = 0.95


def _safe_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(fragments: Mapping[str, object], keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in fragments:
            value = _safe_float(fragments.get(key))
            if value is not None and value >= 0:
                return value
    return None


def _pick_observed_memory_bytes(fragments: Mapping[str, object]) -> Optional[int]:
    """
    Pick a single "observed memory" value from collected fragments.

    We prefer a cgroup based value when available, then fall back to a process resident value.

    Supported keys:
      - memory_current_bytes (generic)
      - cgroup_memory_current_bytes (Linux cgroup v2)
      - process_resident_memory_bytes (generic)
      - rss_bytes (legacy)
    """
    memory_bytes = _first_number(
        fragments,
        (
            "memory_current_bytes",
            "cgroup_memory_current_bytes",
            "process_resident_memory_bytes",
            "rss_bytes",
        ),
    )
    return int(memory_bytes) if memory_bytes is not None else None


def _pick_memory_limit_bytes(fragments: Mapping[str, object]) -> Optional[int]:
    limit_bytes = _first_number(
        fragments,
        (
            "memory_limit_bytes",
            "cgroup_memory_max_bytes",
        ),
    )
    return int(limit_bytes) if limit_bytes is not None else None


def _pick_disk_write_total_bytes(fragments: Mapping[str, object]) -> Optional[float]:
    return _first_number(
        fragments,
        (
            "disk_write_bytes_total",
            "io_write_bytes_total",  # legacy
        ),
    )


class Governor:
    """
    Evaluates sampled resource fragments against configured thresholds.

    Inputs
    - sample_ts: a monotonic timestamp in seconds for the sample moment
    - started_monotonic: the monotonic timestamp in seconds when the run started
    - fragments: a dict of collected metrics (values can come from any collector)

    Outputs
    - GovernorDecision with level NONE, WARN, or PANIC
    """

    def __init__(self, thresholds: Thresholds) -> None:
        self._thresholds = thresholds
        self._has_warned_memory = False

        self._last_disk_write_total_bytes: Optional[float] = None
        self._last_disk_write_sample_timestamp_seconds: Optional[float] = None
        self._disk_write_sustained = SustainedCondition(required_seconds=float(thresholds.disk_write_sustain_seconds))

    def evaluate(self, *, sample_ts: float, fragments: dict, started_monotonic: float) -> GovernorDecision:
        sample_timestamp_seconds = float(sample_ts)
        started_monotonic_seconds = float(started_monotonic)

        observed_memory_bytes = _pick_observed_memory_bytes(fragments)
        if observed_memory_bytes is not None:
            warn_threshold_bytes = int(self._thresholds.warn_memory_bytes)
            max_threshold_bytes = int(self._thresholds.max_memory_bytes)

            if warn_threshold_bytes > 0 and (not self._has_warned_memory) and observed_memory_bytes >= warn_threshold_bytes:
                self._has_warned_memory = True
                return GovernorDecision(
                    level="WARN",
                    policy_id="memory.usage.warn",
                    message=f"Memory usage is high: {observed_memory_bytes} bytes (warn threshold {warn_threshold_bytes})",
                )

            if max_threshold_bytes > 0 and observed_memory_bytes >= max_threshold_bytes:
                return GovernorDecision(
                    level="PANIC",
                    policy_id="memory.usage.panic",
                    message=f"Memory usage exceeded max: {observed_memory_bytes} bytes (max {max_threshold_bytes})",
                )

        # Panic near container limit when both current and limit exist.
        cgroup_current_bytes = _first_number(
            fragments,
            (
                "memory_current_bytes",
                "cgroup_memory_current_bytes",
            ),
        )
        memory_limit_bytes = _pick_memory_limit_bytes(fragments)

        if cgroup_current_bytes is not None and memory_limit_bytes is not None and memory_limit_bytes > 0:
            if cgroup_current_bytes >= _CGROUP_NEAR_LIMIT_RATIO * float(memory_limit_bytes):
                return GovernorDecision(
                    level="PANIC",
                    policy_id="memory.limit.near.panic",
                    message=(
                        f"Memory near limit: {int(cgroup_current_bytes)} bytes "
                        f"(limit {int(memory_limit_bytes)} bytes, threshold {int(_CGROUP_NEAR_LIMIT_RATIO * 100)}%)"
                    ),
                )

        # Runtime timeout panic
        if self._thresholds.max_runtime_seconds is not None:
            elapsed_seconds = sample_timestamp_seconds - started_monotonic_seconds
            if elapsed_seconds >= float(self._thresholds.max_runtime_seconds):
                return GovernorDecision(
                    level="PANIC",
                    policy_id="runtime.timeout.panic",
                    message=(
                        f"Runtime exceeded max: {elapsed_seconds:.2f}s "
                        f"(max {float(self._thresholds.max_runtime_seconds):.2f}s)"
                    ),
                )

        # Disk write sustained panic (uses monotonically increasing totals)
        write_rate_limit = self._thresholds.disk_write_rate_bytes_per_second
        if write_rate_limit is not None and float(write_rate_limit) > 0:
            write_total_bytes = _pick_disk_write_total_bytes(fragments)

            if write_total_bytes is not None:
                if (
                    self._last_disk_write_total_bytes is not None
                    and self._last_disk_write_sample_timestamp_seconds is not None
                ):
                    interval_seconds = sample_timestamp_seconds - float(self._last_disk_write_sample_timestamp_seconds)
                    written_bytes = float(write_total_bytes) - float(self._last_disk_write_total_bytes)

                    if interval_seconds > 0 and written_bytes >= 0:
                        write_rate_bytes_per_second = written_bytes / interval_seconds
                        is_over_limit = write_rate_bytes_per_second >= float(write_rate_limit)

                        is_sustained = self._disk_write_sustained.update(
                            is_true=is_over_limit,
                            timestamp_seconds=sample_timestamp_seconds,
                        )
                        if is_sustained:
                            return GovernorDecision(
                                level="PANIC",
                                policy_id="disk.write_rate.panic",
                                message=(
                                    f"Disk write rate sustained high: {write_rate_bytes_per_second:.1f} B/s "
                                    f"(limit {float(write_rate_limit):.1f} B/s, "
                                    f"sustain {float(self._thresholds.disk_write_sustain_seconds):.1f}s)"
                                ),
                            )
                    else:
                        # Reset sustain tracking on bad intervals.
                        self._disk_write_sustained.update(is_true=False, timestamp_seconds=sample_timestamp_seconds)

                self._last_disk_write_total_bytes = float(write_total_bytes)
                self._last_disk_write_sample_timestamp_seconds = sample_timestamp_seconds

        return GovernorDecision(level="NONE", policy_id=None, message=None)
