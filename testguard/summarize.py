from __future__ import annotations

from testguard.analyze import compute_metrics
from testguard.store.store import RunRecord, RunSummaryRecord
from testguard.store.sqlite_store import SQLiteRunStore


def persist_summary_for_run(store: SQLiteRunStore, run_record: RunRecord) -> RunSummaryRecord:
    samples = store.list_samples(run_record.run_id)
    metrics = compute_metrics(run_record, samples)
    warnings_count = store.count_warnings(run_record.run_id)

    summary = RunSummaryRecord(
        run_id=run_record.run_id,
        peak_rss_bytes=metrics.peak_rss_bytes,
        total_read_bytes=metrics.total_read_bytes,
        total_write_bytes=metrics.total_write_bytes,
        peak_write_rate_bytes_s=metrics.peak_write_rate_bytes_s,
        warnings_count=warnings_count,
    )
    store.upsert_summary(summary)
    return summary
