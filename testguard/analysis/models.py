from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True, kw_only=True)
class RunMetrics:
    """
    Stable per-run summary metrics.

    The top-level fields are the "core" metrics TestGuard uses in list, report, and diffs.

    Additional, platform-specific, or collector-specific metrics go into `extra_metrics`,  keyed by a stable metric
    name. This avoids expanding the dataclass for every new metric.

    Naming rules:
    - Use human-readable suffixes like `_seconds` and `_bytes`.
    - Avoid encoding collector names in the metric key.
      Example: Linux PSI populates general keys like `memory.pressure_peak_percent`.
    """

    duration_seconds: Optional[float]
    peak_memory_bytes: Optional[int]
    total_read_bytes: Optional[int]
    total_write_bytes: Optional[int]
    peak_write_rate_bytes_per_second: Optional[float]
    extra_metrics: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class MetricDelta:
    current: Optional[float]
    baseline: Optional[float]
    delta_absolute: Optional[float]
    delta_percent: Optional[float]


@dataclass(frozen=True, kw_only=True)
class DiffSummary:
    baseline_run_id: Optional[str]
    classification: str  # OK | WARN | REGRESSION | NO_BASELINE
    metric_deltas: Dict[str, MetricDelta]
    recommendations: List[Dict[str, str]]  # {area, message, confidence}
