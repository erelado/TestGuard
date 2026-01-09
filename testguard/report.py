from __future__ import annotations

import json
from typing import List, Optional

from testguard.analyze import RunMetrics, DiffSummary
from testguard.store.store import RunRecord
from testguard.util import runs_dir


def _format_duration_seconds(duration_s: float | None) -> str:
    if duration_s is None:
        return "-"
    if duration_s < 1.0:
        return f"{duration_s:.3f}"
    if duration_s < 10.0:
        return f"{duration_s:.2f}"
    return f"{duration_s:.1f}"


def _format_exit_code(exit_code: int | None) -> str:
    return "-" if exit_code is None else str(exit_code)


def _format_signal(signal_number: int | None) -> str:
    return "-" if signal_number is None else str(signal_number)


def render_list(run_records: List[RunRecord]) -> str:
    # Build display rows first, then compute widths for clean alignment.
    header = [
        "started at",
        "run id",
        "status",
        "duration (sec)",
        "exit code",
        "signal",
        "peak rss",
        "total write",
        "warnings",
    ]

    rows: List[List[str]] = []
    for run in run_records:
        rows.append(
            [
                run.started_at or "-",
                run.run_id or "-",
                run.status or "-",
                _format_duration_seconds(run.duration_s),
                _format_exit_code(run.exit_code),
                _format_signal(run.signal),
                _format_bytes(run.peak_rss_bytes),
                _format_bytes(run.total_write_bytes),
                str(run.warnings_count),
            ]
        )

    all_rows = [header] + rows
    widths: List[int] = []
    for col_index in range(len(header)):
        widths.append(max(len(row[col_index]) for row in all_rows))

    right_align_columns = {3, 4, 5, 6, 7, 8}  # duration, exit, signal, bytes, warnings

    def format_row(row: List[str]) -> str:
        parts: List[str] = []
        for col_index, cell in enumerate(row):
            pad = widths[col_index]
            parts.append(cell.rjust(pad) if col_index in right_align_columns else cell.ljust(pad))
        return "  ".join(parts)

    lines = [format_row(header)]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append(format_row(row))

    return "\n".join(lines)


def _format_bytes(num: object) -> str:
    if num is None:
        return "-"
    value = float(num)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit_index = 0
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1
    return f"{value:.2f}{units[unit_index]}"


def _format_pct(pct: object) -> str:
    if pct is None:
        return "-"
    pct_value = float(pct)
    sign = "+" if pct_value >= 0 else ""
    return f"{sign}{pct_value:.1f}%"


def render_report(run_record: RunRecord, *, current_metrics: RunMetrics, diff_summary: DiffSummary) -> str:
    lines: list[str] = []

    lines.append(f"run_id: {run_record.run_id}")
    lines.append(f"status: {run_record.status}")
    lines.append(f"duration (sec): {_format_duration_seconds(current_metrics.duration_s)}")
    exit_code_text = str(run_record.exit_code) if run_record.exit_code is not None else "-"
    signal_text = str(run_record.signal) if run_record.signal is not None else "-"
    lines.append(f"exit_code: {exit_code_text}  signal: {signal_text}")
    lines.append("")

    lines.append("metrics:")
    lines.append(f"  peak rss: {_format_bytes(current_metrics.peak_rss_bytes)}")
    lines.append(f"  total read: {_format_bytes(current_metrics.total_read_bytes)}")
    lines.append(f"  total write: {_format_bytes(current_metrics.total_write_bytes)}")
    if current_metrics.peak_write_rate_bytes_s is None:
        lines.append("  peak write rate: -")
    else:
        lines.append(f"  peak write rate: {_format_bytes(current_metrics.peak_write_rate_bytes_s)}/s")
    lines.append("")

    lines.append("diff vs baseline:")
    if diff_summary.baseline_run_id is None:
        lines.append("  baseline: -")
        lines.append("  classification: NO_BASELINE")
    else:
        lines.append(f"  baseline: {diff_summary.baseline_run_id}")
        lines.append(f"  classification: {diff_summary.classification}")
        lines.append(f"  duration: {_format_pct(diff_summary.duration_s.delta_pct)}")
        lines.append(f"  peak rss: {_format_pct(diff_summary.peak_rss_bytes.delta_pct)}")
        lines.append(f"  total write: {_format_pct(diff_summary.total_write_bytes.delta_pct)}")
    lines.append("")

    lines.append("recommendations:")
    if not diff_summary.recommendations:
        lines.append("  - none")
    else:
        for rec in diff_summary.recommendations:
            lines.append(f"  - {rec['area']}: {rec['message']} (confidence: {rec['confidence']})")

    return "\n".join(lines)


def _metric_line(name: str, current: Optional[float], baseline: Optional[float], delta_pct: Optional[float]) -> str:
    return f"| {name} | {current if current is not None else '-'} | {baseline if baseline is not None else '-'} | {_format_pct(delta_pct)} |"


def write_run_report_artifacts(
        *,
        run_record: RunRecord,
        current_metrics: RunMetrics,
        diff_summary: DiffSummary,
) -> None:
    run_directory = runs_dir() / run_record.run_id
    run_directory.mkdir(parents=True, exist_ok=True)

    report_json = {
        "run_id": run_record.run_id,
        "status": run_record.status,
        "exit_code": run_record.exit_code,
        "signal": run_record.signal,
        "started_at": run_record.started_at,
        "ended_at": run_record.ended_at,
        "cwd": run_record.cwd,
        "signature_hash": run_record.signature_hash,
        "metrics": {
            "duration_s": current_metrics.duration_s,
            "peak_rss_bytes": current_metrics.peak_rss_bytes,
            "total_read_bytes": current_metrics.total_read_bytes,
            "total_write_bytes": current_metrics.total_write_bytes,
            "peak_write_rate_bytes_s": current_metrics.peak_write_rate_bytes_s,
        },
        "diff": {
            "baseline_run_id": diff_summary.baseline_run_id,
            "classification": diff_summary.classification,
            "duration_s": diff_summary.duration_s.__dict__,
            "peak_rss_bytes": diff_summary.peak_rss_bytes.__dict__,
            "total_read_bytes": diff_summary.total_read_bytes.__dict__,
            "total_write_bytes": diff_summary.total_write_bytes.__dict__,
            "peak_write_rate_bytes_s": diff_summary.peak_write_rate_bytes_s.__dict__,
        },
        "recommendations": diff_summary.recommendations,
    }
    (run_directory / "run_report.json").write_text(json.dumps(report_json, indent=2, sort_keys=True), encoding="utf-8")

    md_lines = []
    md_lines.append("# TestGuard run report")
    md_lines.append("")
    md_lines.append(f"run_id: `{run_record.run_id}`")
    md_lines.append(f"status: `{run_record.status}`")
    md_lines.append(f"duration (sec): `{_format_duration_seconds(current_metrics.duration_s)}`")
    md_lines.append(
        f"duration (sec): `{current_metrics.duration_s if current_metrics.duration_s is not None else '-'}`")
    md_lines.append(f"exit_code: `{run_record.exit_code if run_record.exit_code is not None else '-'}`")
    md_lines.append(f"signal: `{run_record.signal if run_record.signal is not None else '-'}`")
    md_lines.append("")

    md_lines.append("## Metrics")
    md_lines.append("")
    md_lines.append(f"- peak RSS: {_format_bytes(current_metrics.peak_rss_bytes)}")
    md_lines.append(f"- total read: {_format_bytes(current_metrics.total_read_bytes)}")
    md_lines.append(f"- total write: {_format_bytes(current_metrics.total_write_bytes)}")
    md_lines.append(
        f"- peak write rate: {_format_bytes(current_metrics.peak_write_rate_bytes_s)}/s" if current_metrics.peak_write_rate_bytes_s is not None else "- peak write rate: -")
    md_lines.append("")

    md_lines.append("## Diff vs baseline")
    md_lines.append("")
    md_lines.append(f"baseline_run_id: `{diff_summary.baseline_run_id if diff_summary.baseline_run_id else '-'}`")
    md_lines.append(f"classification: `{diff_summary.classification}`")
    md_lines.append("")
    md_lines.append("| metric | current | baseline | delta |")
    md_lines.append("|---|---:|---:|---:|")
    md_lines.append(_metric_line("duration_s", diff_summary.duration_s.current, diff_summary.duration_s.baseline,
                                 diff_summary.duration_s.delta_pct))

    md_lines.append(
        _metric_line("peak_rss_bytes", diff_summary.peak_rss_bytes.current, diff_summary.peak_rss_bytes.baseline,
                     diff_summary.peak_rss_bytes.delta_pct))
    md_lines.append(
        _metric_line("total_read_bytes", diff_summary.total_read_bytes.current, diff_summary.total_read_bytes.baseline,
                     diff_summary.total_read_bytes.delta_pct))
    md_lines.append(_metric_line("total_write_bytes", diff_summary.total_write_bytes.current,
                                 diff_summary.total_write_bytes.baseline, diff_summary.total_write_bytes.delta_pct))
    md_lines.append(_metric_line("peak_write_rate_bytes_s", diff_summary.peak_write_rate_bytes_s.current,
                                 diff_summary.peak_write_rate_bytes_s.baseline,
                                 diff_summary.peak_write_rate_bytes_s.delta_pct))
    md_lines.append("")

    md_lines.append("## Recommendations")
    md_lines.append("")
    if not diff_summary.recommendations:
        md_lines.append("- none")
    else:
        for rec in diff_summary.recommendations:
            md_lines.append(f"- {rec['area']}: {rec['message']} (confidence: {rec['confidence']})")

    (run_directory / "run_report.md").write_text("\n".join(md_lines), encoding="utf-8")
