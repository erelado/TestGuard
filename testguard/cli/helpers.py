from __future__ import annotations

from typing import Optional

from testguard.analysis import DiffSummary, RunMetrics, compute_metrics, diff_metrics, no_baseline_diff
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import RunRecord


def load_metrics_and_diff(
    *,
    store: SQLiteRunStore,
    run_id: str,
    baseline_run_id: Optional[str],
) -> tuple[RunRecord, RunMetrics, DiffSummary]:
    run_record = store.load_run(run_id=run_id)
    current_samples = store.list_samples(run_id=run_id)
    current_metrics = compute_metrics(run_record, current_samples)

    chosen_baseline_run_id = baseline_run_id
    if chosen_baseline_run_id is None:
        chosen_baseline_run_id = store.find_baseline_run_id(run_record.signature_hash, exclude_run_id=run_id)

    if chosen_baseline_run_id is None:
        return run_record, current_metrics, no_baseline_diff(current_metrics)

    baseline_record = store.load_run(run_id=chosen_baseline_run_id)
    baseline_samples = store.list_samples(run_id=chosen_baseline_run_id)
    baseline_metrics = compute_metrics(baseline_record, baseline_samples)

    diff_summary = diff_metrics(current_metrics, baseline_metrics, baseline_run_id=chosen_baseline_run_id)
    return run_record, current_metrics, diff_summary