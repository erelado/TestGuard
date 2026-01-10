from __future__ import annotations

from typing import Dict, List, Optional

from testguard.analysis.models import DiffSummary, MetricDelta, RunMetrics

METRIC_DURATION_SECONDS = "time.duration_seconds"
METRIC_MEMORY_PEAK_BYTES = "memory.peak_bytes"
METRIC_DISK_READ_BYTES_TOTAL = "disk.read_bytes_total"
METRIC_DISK_WRITE_BYTES_TOTAL = "disk.write_bytes_total"
METRIC_DISK_WRITE_RATE_PEAK_BPS = "disk.write_rate_peak_bytes_per_second"


def _safe_percent(delta_absolute: float, baseline: float) -> Optional[float]:
    if baseline == 0.0:
        return None
    return (delta_absolute / baseline) * 100.0


def _metric_delta(current: Optional[float], baseline: Optional[float]) -> MetricDelta:
    if current is None or baseline is None:
        return MetricDelta(current=current, baseline=baseline, delta_absolute=None, delta_percent=None)

    delta_absolute = current - baseline
    delta_percent = _safe_percent(delta_absolute, baseline)
    return MetricDelta(
        current=current,
        baseline=baseline,
        delta_absolute=delta_absolute,
        delta_percent=delta_percent,
    )


def _runmetrics_to_value_map(metrics: RunMetrics) -> Dict[str, Optional[float]]:
    values: Dict[str, Optional[float]] = {
        METRIC_DURATION_SECONDS: metrics.duration_seconds,
        METRIC_MEMORY_PEAK_BYTES: float(metrics.peak_memory_bytes) if metrics.peak_memory_bytes is not None else None,
        METRIC_DISK_READ_BYTES_TOTAL: float(metrics.total_read_bytes) if metrics.total_read_bytes is not None else None,
        METRIC_DISK_WRITE_BYTES_TOTAL: float(metrics.total_write_bytes) if metrics.total_write_bytes is not None else None,
        METRIC_DISK_WRITE_RATE_PEAK_BPS: metrics.peak_write_rate_bytes_per_second,
    }

    # Merge extra metrics without forcing schema changes.
    for metric_key, value in metrics.extra_metrics.items():
        values[metric_key] = float(value)

    return values


def diff_metrics(current: RunMetrics, baseline: RunMetrics, baseline_run_id: str) -> DiffSummary:
    current_values = _runmetrics_to_value_map(current)
    baseline_values = _runmetrics_to_value_map(baseline)

    metric_deltas: Dict[str, MetricDelta] = {}
    for metric_key in sorted(set(current_values.keys()) | set(baseline_values.keys())):
        metric_deltas[metric_key] = _metric_delta(current_values.get(metric_key), baseline_values.get(metric_key))

    classification = "OK"
    recommendations: List[Dict[str, str]] = []

    memory_delta = metric_deltas.get(METRIC_MEMORY_PEAK_BYTES)
    if memory_delta and memory_delta.delta_percent is not None and memory_delta.delta_percent >= 15.0:
        classification = "WARN"
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

    duration_delta = metric_deltas.get(METRIC_DURATION_SECONDS)
    if duration_delta and duration_delta.delta_percent is not None and duration_delta.delta_percent >= 20.0:
        if classification == "OK":
            classification = "WARN"
        recommendations.append(
            {
                "area": "time",
                "message": (
                    "Runtime increased vs baseline. Consider caching expensive setup, "
                    "reducing contention, or isolating slow suites."
                ),
                "confidence": "medium",
            }
        )

    write_delta = metric_deltas.get(METRIC_DISK_WRITE_BYTES_TOTAL)
    if write_delta and write_delta.delta_percent is not None and write_delta.delta_percent >= 100.0:
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
        metric_deltas=metric_deltas,
        recommendations=recommendations,
    )


def no_baseline_diff(current: RunMetrics) -> DiffSummary:
    current_values = _runmetrics_to_value_map(current)
    metric_deltas: Dict[str, MetricDelta] = {
        metric_key: _metric_delta(value, None) for metric_key, value in current_values.items()
    }

    return DiffSummary(
        baseline_run_id=None,
        classification="NO_BASELINE",
        metric_deltas=metric_deltas,
        recommendations=[],
    )
