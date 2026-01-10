from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

from testguard.analysis.models import RunMetrics
from testguard.store.store import RunRecord, SampleRecord

METRIC_MEMORY_PEAK_BYTES = "memory.peak_bytes"
METRIC_DISK_READ_BYTES_TOTAL = "disk.read_bytes_total"
METRIC_DISK_WRITE_BYTES_TOTAL = "disk.write_bytes_total"
METRIC_DISK_WRITE_RATE_PEAK_BPS = "disk.write_rate_peak_bytes_per_second"

# Generalized "memory pressure" keys.
# On Linux: populated from PSI (avg10). On other platforms: populated by whatever collector you add later.
METRIC_MEMORY_PRESSURE_PEAK_PERCENT = "memory.pressure_peak_percent"
METRIC_MEMORY_STALL_PEAK_PERCENT = "memory.stall_peak_percent"


def _parse_sample_payload(sample: SampleRecord) -> Dict[str, Any]:
    try:
        return json.loads(sample.payload_json)
    except Exception:
        return {}


def _extract_int(payload: dict, key: str) -> Optional[int]:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_float(payload: dict, key: str) -> Optional[float]:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_observed_memory_bytes(payload: dict) -> Optional[int]:
    # Prefer cgroup v2 memory.current when available, otherwise fall back to RSS.
    for key in ("cgroup_memory_current_bytes", "rss_bytes"):
        value = _extract_int(payload, key)
        if value is not None and value >= 0:
            return value
    return None


def compute_metrics(run_record: RunRecord, samples: Iterable[SampleRecord]) -> RunMetrics:
    peak_memory_bytes: Optional[int] = None

    first_read_bytes_total: Optional[int] = None
    last_read_bytes_total: Optional[int] = None

    first_write_bytes_total: Optional[int] = None
    last_write_bytes_total: Optional[int] = None

    last_monotonic_timestamp_seconds: Optional[float] = None
    last_write_bytes_total_for_rate: Optional[int] = None
    peak_write_rate_bytes_per_second: Optional[float] = None

    memory_pressure_peak_percent: Optional[float] = None
    memory_stall_peak_percent: Optional[float] = None

    for sample in samples:
        payload = _parse_sample_payload(sample)

        # Generalized memory pressure metrics.
        # Linux PSI collector writes:
        # - psi_memory_some_avg10 -> memory.pressure_peak_percent
        # - psi_memory_full_avg10 -> memory.stall_peak_percent
        pressure_candidate = _extract_float(payload, "psi_memory_some_avg10")
        if pressure_candidate is not None:
            if memory_pressure_peak_percent is None or pressure_candidate > memory_pressure_peak_percent:
                memory_pressure_peak_percent = pressure_candidate

        stall_candidate = _extract_float(payload, "psi_memory_full_avg10")
        if stall_candidate is not None:
            if memory_stall_peak_percent is None or stall_candidate > memory_stall_peak_percent:
                memory_stall_peak_percent = stall_candidate

        observed_memory_bytes = _pick_observed_memory_bytes(payload)
        if observed_memory_bytes is not None:
            if peak_memory_bytes is None or observed_memory_bytes > peak_memory_bytes:
                peak_memory_bytes = observed_memory_bytes

        read_bytes_total = _extract_int(payload, "io_read_bytes_total")
        if read_bytes_total is not None:
            if first_read_bytes_total is None:
                first_read_bytes_total = read_bytes_total
            last_read_bytes_total = read_bytes_total

        write_bytes_total = _extract_int(payload, "io_write_bytes_total")
        if write_bytes_total is not None:
            if first_write_bytes_total is None:
                first_write_bytes_total = write_bytes_total
            last_write_bytes_total = write_bytes_total

            if last_monotonic_timestamp_seconds is not None and last_write_bytes_total_for_rate is not None:
                current_monotonic_timestamp_seconds = float(sample.ts_monotonic)
                delta_seconds = current_monotonic_timestamp_seconds - last_monotonic_timestamp_seconds
                delta_write_bytes = write_bytes_total - last_write_bytes_total_for_rate

                if delta_seconds > 0.0 and delta_write_bytes >= 0:
                    rate_bytes_per_second = delta_write_bytes / delta_seconds
                    if (
                        peak_write_rate_bytes_per_second is None
                        or rate_bytes_per_second > peak_write_rate_bytes_per_second
                    ):
                        peak_write_rate_bytes_per_second = rate_bytes_per_second

            last_monotonic_timestamp_seconds = float(sample.ts_monotonic)
            last_write_bytes_total_for_rate = write_bytes_total

    total_read_bytes: Optional[int] = None
    if (
        first_read_bytes_total is not None
        and last_read_bytes_total is not None
        and last_read_bytes_total >= first_read_bytes_total
    ):
        total_read_bytes = last_read_bytes_total - first_read_bytes_total

    total_write_bytes: Optional[int] = None
    if (
        first_write_bytes_total is not None
        and last_write_bytes_total is not None
        and last_write_bytes_total >= first_write_bytes_total
    ):
        total_write_bytes = last_write_bytes_total - first_write_bytes_total

    extra_metrics: Dict[str, float] = {}
    if memory_pressure_peak_percent is not None:
        extra_metrics[METRIC_MEMORY_PRESSURE_PEAK_PERCENT] = memory_pressure_peak_percent
    if memory_stall_peak_percent is not None:
        extra_metrics[METRIC_MEMORY_STALL_PEAK_PERCENT] = memory_stall_peak_percent

    # Core metrics are stored as first-class fields for convenient access and reporting.
    # The metric key constants exist mainly for diff/report table rendering.
    return RunMetrics(
        duration_seconds=run_record.duration_s,
        peak_memory_bytes=peak_memory_bytes,
        total_read_bytes=total_read_bytes,
        total_write_bytes=total_write_bytes,
        peak_write_rate_bytes_per_second=peak_write_rate_bytes_per_second,
        extra_metrics=extra_metrics,
    )