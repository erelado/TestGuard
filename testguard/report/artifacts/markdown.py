from __future__ import annotations

from pathlib import Path
from typing import Optional

from testguard.analysis import DiffSummary, RunMetrics
from testguard.report.formatting import (
    format_bytes,
    format_duration_seconds,
    format_exit_code,
    format_signal,
)
from testguard.store.store import RunRecord


def _metric_line(
    metric_name: str,
    current: Optional[float],
    baseline: Optional[float],
    delta_percent: Optional[float],
) -> str:
    current_text = current if current is not None else "-"
    baseline_text = baseline if baseline is not None else "-"
    delta_text = "-" if delta_percent is None else f"{delta_percent:+.1f}%"
    return f"| {metric_name} | {current_text} | {baseline_text} | {delta_text} |"


def build_run_report_markdown(
    *,
    run_record: RunRecord,
    current_metrics: RunMetrics,
    diff_summary: DiffSummary,
) -> str:
    extra_metrics = getattr(current_metrics, "extra_metrics", {}) or {}
    metric_deltas = getattr(diff_summary, "metric_deltas", None)

    lines: list[str] = []
    lines.append("# TestGuard run report")
    lines.append("")
    lines.append(f"run_id: `{run_record.run_id}`")
    lines.append(f"status: `{run_record.status}`")
    lines.append(f"duration (sec): `{format_duration_seconds(current_metrics.duration_seconds)}`")
    lines.append(f"exit_code: `{format_exit_code(run_record.exit_code)}`")
    lines.append(f"signal: `{format_signal(run_record.signal)}`")
    lines.append("")

    lines.append("## Metrics")
    lines.append("")
    lines.append(f"- peak memory: {format_bytes(current_metrics.peak_memory_bytes)}")
    lines.append(f"- total read: {format_bytes(current_metrics.total_read_bytes)}")
    lines.append(f"- total write: {format_bytes(current_metrics.total_write_bytes)}")
    if current_metrics.peak_write_rate_bytes_per_second is None:
        lines.append("- peak write rate: -")
    else:
        lines.append(f"- peak write rate: {format_bytes(current_metrics.peak_write_rate_bytes_per_second)}/s")

    for metric_key, metric_value in extra_metrics.items():
        lines.append(f"- {metric_key}: {metric_value:.2f}")

    lines.append("")
    lines.append("## Diff vs baseline")
    lines.append("")
    lines.append(f"baseline_run_id: `{diff_summary.baseline_run_id if diff_summary.baseline_run_id else '-'}`")
    lines.append(f"classification: `{diff_summary.classification}`")
    lines.append("")
    lines.append("| metric | current | baseline | delta |")
    lines.append("|---|---:|---:|---:|")

    if metric_deltas:
        for metric_key, delta in metric_deltas.items():
            lines.append(_metric_line(metric_key, delta.current, delta.baseline, delta.delta_percent))

    lines.append("")
    lines.append("## Recommendations")
    lines.append("")
    if not diff_summary.recommendations:
        lines.append("- none")
    else:
        for rec in diff_summary.recommendations:
            lines.append(f"- {rec['area']}: {rec['message']} (confidence: {rec['confidence']})")

    return "\n".join(lines)


def write_run_report_markdown(
    *,
    run_directory: Path,
    run_record: RunRecord,
    current_metrics: RunMetrics,
    diff_summary: DiffSummary,
) -> None:
    markdown_text = build_run_report_markdown(
        run_record=run_record,
        current_metrics=current_metrics,
        diff_summary=diff_summary,
    )
    (run_directory / "run_report.md").write_text(markdown_text, encoding="utf-8")