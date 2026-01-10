from __future__ import annotations

from typing import List

from testguard.analysis import DiffSummary, RunMetrics
from testguard.report.formatting import (
    format_bytes,
    format_duration_seconds,
    format_exit_code,
    format_percent,
    format_signal,
)
from testguard.store.store import RunRecord


def render_list(run_records: List[RunRecord]) -> str:
    header = [
        "started at",
        "run id",
        "status",
        "duration (sec)",
        "exit code",
        "signal",
        "peak memory",
        "total write",
        "warnings",
    ]

    rows: List[List[str]] = []
    for run_record in run_records:
        rows.append(
            [
                run_record.started_at or "-",
                run_record.run_id or "-",
                run_record.status or "-",
                format_duration_seconds(run_record.duration_s),
                format_exit_code(run_record.exit_code),
                format_signal(run_record.signal),
                format_bytes(run_record.peak_rss_bytes),  # DB column name stays as-is for now
                format_bytes(run_record.total_write_bytes),
                str(run_record.warnings_count),
            ]
        )

    all_rows = [header] + rows
    widths = [max(len(row[i]) for row in all_rows) for i in range(len(header))]
    right_align_columns = {3, 4, 5, 6, 7, 8}

    def format_row(row: List[str]) -> str:
        parts: List[str] = []
        for column_index, cell in enumerate(row):
            pad = widths[column_index]
            parts.append(cell.rjust(pad) if column_index in right_align_columns else cell.ljust(pad))
        return "  ".join(parts)

    lines = [format_row(header)]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append(format_row(row))
    return "\n".join(lines)


def render_report(
        run_record: RunRecord,
        *,
        current_metrics: RunMetrics,
        diff_summary: DiffSummary,
) -> str:
    lines: list[str] = []

    lines.append(f"run_id: {run_record.run_id}")
    lines.append(f"status: {run_record.status}")
    lines.append(f"duration (sec): {format_duration_seconds(current_metrics.duration_seconds)}")

    lines.append(
        f"exit_code: {format_exit_code(run_record.exit_code)}  signal: {format_signal(run_record.signal)}"
    )
    lines.append("")

    lines.append("metrics:")
    lines.append(f"  peak memory: {format_bytes(current_metrics.peak_memory_bytes)}")
    lines.append(f"  total read: {format_bytes(current_metrics.total_read_bytes)}")
    lines.append(f"  total write: {format_bytes(current_metrics.total_write_bytes)}")

    if current_metrics.peak_write_rate_bytes_per_second is None:
        lines.append("  peak write rate: -")
    else:
        lines.append(
            f"  peak write rate: {format_bytes(current_metrics.peak_write_rate_bytes_per_second)}/s"
        )

    # Forward-compatible, only prints when present. (Once RunMetrics has extra_metrics, this will show them.)
    extra_metrics = getattr(current_metrics, "extra_metrics", {}) or {}
    for key, value in extra_metrics.items():
        lines.append(f"  {key}: {value:.2f}")

    lines.append("")
    lines.append("diff vs baseline:")
    if diff_summary.baseline_run_id is None:
        lines.append("  baseline: -")
        lines.append("  classification: NO_BASELINE")
    else:
        lines.append(f"  baseline: {diff_summary.baseline_run_id}")
        lines.append(f"  classification: {diff_summary.classification}")

        # Preferred generalized form
        metric_deltas = getattr(diff_summary, "metric_deltas", None)
        if metric_deltas:
            for metric_key, delta in metric_deltas.items():
                lines.append(f"  {metric_key}: {format_percent(delta.delta_percent)}")
        else:
            # Backward-compat fallback if you still have explicit fields
            lines.append(f"  duration: {format_percent(diff_summary.duration_seconds.delta_percent)}")
            lines.append(f"  peak memory: {format_percent(diff_summary.peak_memory_bytes.delta_percent)}")
            lines.append(f"  total write: {format_percent(diff_summary.total_write_bytes.delta_percent)}")

    lines.append("")
    lines.append("recommendations:")
    if not diff_summary.recommendations:
        lines.append("  - none")
    else:
        for rec in diff_summary.recommendations:
            lines.append(f"  - {rec['area']}: {rec['message']} (confidence: {rec['confidence']})")

    return "\n".join(lines)
