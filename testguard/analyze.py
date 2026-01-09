from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from testguard.store.store import RunRecord, SampleRecord


@dataclass(frozen=True, kw_only=True)
class RunMetrics:
    duration_s: Optional[float]
    peak_rss_bytes: Optional[int]  # peak observed memory (cgroup current preferred, else RSS)
    total_read_bytes: Optional[int]
    total_write_bytes: Optional[int]
    peak_write_rate_bytes_s: Optional[float]
    psi_memory_some_avg10_peak: Optional[float] = None
    psi_memory_full_avg10_peak: Optional[float] = None


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


def _parse_sample_payload(sample: SampleRecord) -> Dict[str, Any]:
    try:
        return json.loads(sample.payload_json)
    except Exception:
        return {}


def _pick_observed_memory_bytes(payload: dict) -> Optional[int]:
    # Prefer cgroup v2 memory.current when available, otherwise fall back to RSS.
    for key in ("cgroup_memory_current_bytes", "rss_bytes"):
        value = _extract_int(payload, key)
        if value is not None and value >= 0:
            return value
    return None


@dataclass(frozen=True, kw_only=True)
class MetricDelta:
    current: Optional[float]
    baseline: Optional[float]
    delta_abs: Optional[float]
    delta_pct: Optional[float]


@dataclass(frozen=True, kw_only=True)
class DiffSummary:
    baseline_run_id: Optional[str]
    classification: str  # OK | WARN | REGRESSION | NO_BASELINE
    duration_s: MetricDelta
    peak_rss_bytes: MetricDelta
    total_write_bytes: MetricDelta
    total_read_bytes: MetricDelta
    peak_write_rate_bytes_s: MetricDelta
    recommendations: List[Dict[str, str]]  # {area, message, confidence}


def _safe_pct(delta_abs: float, baseline: float) -> Optional[float]:
    if baseline == 0:
        return None
    return (delta_abs / baseline) * 100.0


def _metric_delta(current: Optional[float], baseline: Optional[float]) -> MetricDelta:
    if current is None or baseline is None:
        return MetricDelta(current=current, baseline=baseline, delta_abs=None, delta_pct=None)
    delta_abs = current - baseline
    delta_pct = _safe_pct(delta_abs, baseline)
    return MetricDelta(current=current, baseline=baseline, delta_abs=delta_abs, delta_pct=delta_pct)


def compute_metrics(run_record: RunRecord, samples: Iterable[SampleRecord]) -> RunMetrics:
    peak_memory_bytes: Optional[int] = None

    io_first_read: Optional[int] = None
    io_last_read: Optional[int] = None
    io_first_write: Optional[int] = None
    io_last_write: Optional[int] = None

    last_ts: Optional[float] = None
    last_write_bytes: Optional[int] = None
    peak_write_rate_bytes_s: Optional[float] = None

    psi_some_avg10_peak: Optional[float] = None
    psi_full_avg10_peak: Optional[float] = None

    for sample in samples:
        payload = _parse_sample_payload(sample)

        psi_some = _extract_float(payload, "psi_memory_some_avg10")
        if psi_some is not None:
            if psi_some_avg10_peak is None or psi_some > psi_some_avg10_peak:
                psi_some_avg10_peak = psi_some

        psi_full = _extract_float(payload, "psi_memory_full_avg10")
        if psi_full is not None:
            if psi_full_avg10_peak is None or psi_full > psi_full_avg10_peak:
                psi_full_avg10_peak = psi_full

        memory_bytes = _pick_observed_memory_bytes(payload)
        if memory_bytes is not None:
            if peak_memory_bytes is None or memory_bytes > peak_memory_bytes:
                peak_memory_bytes = memory_bytes

        read_bytes = _extract_int(payload, "io_read_bytes_total")
        if read_bytes is not None:
            if io_first_read is None:
                io_first_read = read_bytes
            io_last_read = read_bytes

        write_bytes = _extract_int(payload, "io_write_bytes_total")
        if write_bytes is not None:
            if io_first_write is None:
                io_first_write = write_bytes
            io_last_write = write_bytes

            if last_ts is not None and last_write_bytes is not None:
                delta_t = float(sample.ts_monotonic) - float(last_ts)
                delta_write = write_bytes - last_write_bytes
                if delta_t > 0 and delta_write >= 0:
                    rate = delta_write / delta_t
                    if peak_write_rate_bytes_s is None or rate > peak_write_rate_bytes_s:
                        peak_write_rate_bytes_s = rate

            last_ts = float(sample.ts_monotonic)
            last_write_bytes = write_bytes

    total_read_bytes: Optional[int] = None
    if io_first_read is not None and io_last_read is not None and io_last_read >= io_first_read:
        total_read_bytes = io_last_read - io_first_read

    total_write_bytes: Optional[int] = None
    if io_first_write is not None and io_last_write is not None and io_last_write >= io_first_write:
        total_write_bytes = io_last_write - io_first_write

    return RunMetrics(
        duration_s=run_record.duration_s,
        peak_rss_bytes=peak_memory_bytes,
        total_read_bytes=total_read_bytes,
        total_write_bytes=total_write_bytes,
        peak_write_rate_bytes_s=peak_write_rate_bytes_s,
        psi_memory_some_avg10_peak=psi_some_avg10_peak,
        psi_memory_full_avg10_peak=psi_full_avg10_peak,
    )


def diff_metrics(current: RunMetrics, baseline: RunMetrics, baseline_run_id: str) -> DiffSummary:
    duration = _metric_delta(current.duration_s, baseline.duration_s)

    peak_memory = _metric_delta(
        float(current.peak_rss_bytes) if current.peak_rss_bytes is not None else None,
        float(baseline.peak_rss_bytes) if baseline.peak_rss_bytes is not None else None,
    )

    total_write = _metric_delta(
        float(current.total_write_bytes) if current.total_write_bytes is not None else None,
        float(baseline.total_write_bytes) if baseline.total_write_bytes is not None else None,
    )

    total_read = _metric_delta(
        float(current.total_read_bytes) if current.total_read_bytes is not None else None,
        float(baseline.total_read_bytes) if baseline.total_read_bytes is not None else None,
    )

    peak_write_rate = _metric_delta(current.peak_write_rate_bytes_s, baseline.peak_write_rate_bytes_s)

    classification = "OK"
    recommendations: List[Dict[str, str]] = []

    memory_pct = peak_memory.delta_pct
    if memory_pct is not None and memory_pct >= 15.0:
        classification = "WARN" if classification == "OK" else classification
        recommendations.append(
            {
                "area": "memory",
                "message": (
                    "Peak memory increased vs baseline. Check recent fixture or test data changes, "
                    "reduce materialization, or split large datasets."
                ),
                "confidence": "medium",
            }
        )

    dur_pct = duration.delta_pct
    if dur_pct is not None and dur_pct >= 20.0:
        classification = "WARN" if classification == "OK" else classification
        recommendations.append(
            {
                "area": "time",
                "message": (
                    "Runtime increased vs baseline. Consider reducing test parallelism contention, "
                    "caching expensive setup, or isolating slow suites."
                ),
                "confidence": "medium",
            }
        )

    write_pct = total_write.delta_pct
    if write_pct is not None and write_pct >= 100.0:
        classification = "REGRESSION"
        recommendations.append(
            {
                "area": "disk",
                "message": (
                    "Disk write volume spiked vs baseline. Reduce verbose logs, redirect artifacts, "
                    "or use tmpfs for ephemeral output."
                ),
                "confidence": "high",
            }
        )

    return DiffSummary(
        baseline_run_id=baseline_run_id,
        classification=classification,
        duration_s=duration,
        peak_rss_bytes=peak_memory,
        total_write_bytes=total_write,
        total_read_bytes=total_read,
        peak_write_rate_bytes_s=peak_write_rate,
        recommendations=recommendations,
    )


def no_baseline_diff(current: RunMetrics) -> DiffSummary:
    return DiffSummary(
        baseline_run_id=None,
        classification="NO_BASELINE",
        duration_s=_metric_delta(current.duration_s, None),
        peak_rss_bytes=_metric_delta(
            float(current.peak_rss_bytes) if current.peak_rss_bytes is not None else None,
            None,
        ),
        total_write_bytes=_metric_delta(
            float(current.total_write_bytes) if current.total_write_bytes is not None else None,
            None,
        ),
        total_read_bytes=_metric_delta(
            float(current.total_read_bytes) if current.total_read_bytes is not None else None,
            None,
        ),
        peak_write_rate_bytes_s=_metric_delta(current.peak_write_rate_bytes_s, None),
        recommendations=[],
    )
