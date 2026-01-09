from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from testguard.store.store import RunRecord, SampleRecord


@dataclass(frozen=True)
class RunMetrics:
    duration_s: Optional[float]
    peak_rss_bytes: Optional[int]
    total_read_bytes: Optional[int]
    total_write_bytes: Optional[int]
    peak_write_rate_bytes_s: Optional[float]


@dataclass(frozen=True)
class MetricDelta:
    current: Optional[float]
    baseline: Optional[float]
    delta_abs: Optional[float]
    delta_pct: Optional[float]


@dataclass(frozen=True)
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


def _parse_sample_payload(sample: SampleRecord) -> Dict[str, Any]:
    try:
        return json.loads(sample.payload_json)
    except Exception:
        return {}


def compute_metrics(run_record: RunRecord, samples: List[SampleRecord]) -> RunMetrics:
    duration_s = run_record.duration_s

    if not samples:
        return RunMetrics(
            duration_s=duration_s,
            peak_rss_bytes=None,
            total_read_bytes=None,
            total_write_bytes=None,
            peak_write_rate_bytes_s=None,
        )

    parsed = [(_parse_sample_payload(s), s.ts_monotonic) for s in samples]

    def iter_numbers(key: str):
        for payload, _ts in parsed:
            value = payload.get(key)
            if isinstance(value, (int, float)):
                yield float(value)

    peak_rss = None
    rss_values = list(iter_numbers("rss_bytes"))
    if rss_values:
        peak_rss = int(max(rss_values))

    def first_last_total(key: str) -> Optional[int]:
        values: List[Tuple[float, float]] = []
        for payload, ts in parsed:
            raw = payload.get(key)
            if isinstance(raw, (int, float)):
                values.append((ts, float(raw)))
        if len(values) < 2:
            return None
        values.sort(key=lambda x: x[0])
        start = values[0][1]
        end = values[-1][1]
        total = end - start
        if total < 0:
            return None
        return int(total)

    total_read = first_last_total("io_read_bytes_total")
    total_write = first_last_total("io_write_bytes_total")

    peak_write_rate = None
    write_points: List[Tuple[float, float]] = []
    for payload, ts in parsed:
        raw = payload.get("io_write_bytes_total")
        if isinstance(raw, (int, float)):
            write_points.append((ts, float(raw)))
    write_points.sort(key=lambda x: x[0])

    if len(write_points) >= 2:
        best = 0.0
        for (t0, w0), (t1, w1) in zip(write_points, write_points[1:]):
            dt = t1 - t0
            dw = w1 - w0
            if dt <= 0:
                continue
            if dw < 0:
                continue
            rate = dw / dt
            if rate > best:
                best = rate
        peak_write_rate = best if best > 0 else None

    return RunMetrics(
        duration_s=duration_s,
        peak_rss_bytes=peak_rss,
        total_read_bytes=total_read,
        total_write_bytes=total_write,
        peak_write_rate_bytes_s=peak_write_rate,
    )


def diff_metrics(current: RunMetrics, baseline: RunMetrics, baseline_run_id: str) -> DiffSummary:
    duration = _metric_delta(current.duration_s, baseline.duration_s)
    peak_rss = _metric_delta(
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

    def pct(delta: MetricDelta) -> Optional[float]:
        return delta.delta_pct

    rss_pct = pct(peak_rss)
    if rss_pct is not None and rss_pct >= 15.0:
        classification = "WARN" if classification == "OK" else classification
        recommendations.append(
            {
                "area": "memory",
                "message": "Peak memory increased vs baseline. Check recent fixture or test data changes, "
                           "reduce materialization, or split large datasets.",
                "confidence": "medium",
            }
        )

    dur_pct = pct(duration)
    if dur_pct is not None and dur_pct >= 20.0:
        classification = "WARN" if classification == "OK" else classification
        recommendations.append(
            {
                "area": "time",
                "message": "Runtime increased vs baseline. Consider reducing test parallelism contention, "
                           "caching expensive setup, or isolating slow suites.",
                "confidence": "medium",
            }
        )

    write_pct = pct(total_write)
    if write_pct is not None and write_pct >= 100.0:
        classification = "REGRESSION"
        recommendations.append(
            {
                "area": "disk",
                "message": "Disk write volume spiked vs baseline. Reduce verbose logs, redirect artifacts, "
                           "or use tmpfs for ephemeral output.",
                "confidence": "high",
            }
        )

    return DiffSummary(
        baseline_run_id=baseline_run_id,
        classification=classification,
        duration_s=duration,
        peak_rss_bytes=peak_rss,
        total_write_bytes=total_write,
        total_read_bytes=total_read,
        peak_write_rate_bytes_s=peak_write_rate,
        recommendations=recommendations,
    )


def no_baseline_diff(current: RunMetrics) -> DiffSummary:
    empty = MetricDelta(current=None, baseline=None, delta_abs=None, delta_pct=None)
    return DiffSummary(
        baseline_run_id=None,
        classification="NO_BASELINE",
        duration_s=_metric_delta(current.duration_s, None),
        peak_rss_bytes=empty,
        total_write_bytes=empty,
        total_read_bytes=empty,
        peak_write_rate_bytes_s=empty,
        recommendations=[],
    )
