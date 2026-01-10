from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from testguard.analysis import DiffSummary, RunMetrics
from testguard.store.store import RunRecord


def _safe_parse_run_config(run_config_json: str) -> Dict[str, Any]:
    if not run_config_json:
        return {}
    try:
        obj = json.loads(run_config_json)
        return obj if isinstance(obj, dict) else {"raw": run_config_json}
    except Exception:
        return {"raw": run_config_json}


def build_run_report_json(
    *,
    run_record: RunRecord,
    current_metrics: RunMetrics,
    diff_summary: DiffSummary,
) -> Dict[str, Any]:
    extra_metrics = getattr(current_metrics, "extra_metrics", {}) or {}
    metric_deltas = getattr(diff_summary, "metric_deltas", None)

    run_config = _safe_parse_run_config(run_record.run_config_json)
    signature_label = run_config.get("signature_label")
    tags = run_config.get("tags") if isinstance(run_config.get("tags"), dict) else {}

    return {
        "run_id": run_record.run_id,
        "status": run_record.status,
        "exit_code": run_record.exit_code,
        "signal": run_record.signal,
        "started_at": run_record.started_at,
        "ended_at": run_record.ended_at,
        "cwd": run_record.cwd,
        "signature_hash": run_record.signature_hash,
        "signature_label": signature_label,
        "tags": tags,
        "run_config": run_config,
        "metrics": {
            "duration_seconds": current_metrics.duration_seconds,
            "peak_memory_bytes": current_metrics.peak_memory_bytes,
            "total_read_bytes": current_metrics.total_read_bytes,
            "total_write_bytes": current_metrics.total_write_bytes,
            "peak_write_rate_bytes_per_second": current_metrics.peak_write_rate_bytes_per_second,
            "extra_metrics": extra_metrics,
        },
        "diff": {
            "baseline_run_id": diff_summary.baseline_run_id,
            "classification": diff_summary.classification,
            "metric_deltas": {key: delta.__dict__ for key, delta in metric_deltas.items()} if metric_deltas else None,
        },
        "recommendations": diff_summary.recommendations,
    }


def write_run_report_json(
    *,
    run_directory: Path,
    run_record: RunRecord,
    current_metrics: RunMetrics,
    diff_summary: DiffSummary,
) -> None:
    report = build_run_report_json(
        run_record=run_record,
        current_metrics=current_metrics,
        diff_summary=diff_summary,
    )
    (run_directory / "run_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
