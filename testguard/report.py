from __future__ import annotations

import json
from typing import List

from testguard.store.store import RunRecord
from testguard.util import runs_dir


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


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
    return "" if signal_number is None else str(signal_number)


def render_list(run_records: List[RunRecord]) -> str:
    # Build display rows first, then compute widths for clean alignment.
    header = ["started at", "run id", "status", "duration (sec)", "exit code", "signal"]

    rows: List[List[str]] = []
    for record in run_records:
        rows.append(
            [
                record.started_at,
                record.run_id,
                record.status,
                _format_duration_seconds(record.duration_s),
                _format_exit_code(record.exit_code),
                _format_signal(record.signal),
            ]
        )

    all_rows = [header] + rows
    col_count = len(header)

    widths = []
    for col_index in range(col_count):
        widths.append(max(len(row[col_index]) for row in all_rows))

    # Right align numeric-ish columns.
    right_align = {3, 4, 5}

    def format_row(values: List[str]) -> str:
        parts = []
        for col_index, value in enumerate(values):
            width = widths[col_index]
            if col_index in right_align:
                parts.append(value.rjust(width))
            else:
                parts.append(value.ljust(width))
        return "  ".join(parts)

    lines = [format_row(header), format_row(["-" * w for w in widths])]
    lines.extend(format_row(row) for row in rows)
    return "\n".join(lines)


def render_report(run_record: RunRecord) -> str:
    command_argv = json.loads(run_record.command_argv_json)
    host_facts = json.loads(run_record.host_facts_json)

    artifact_dir = runs_dir() / run_record.run_id

    lines: List[str] = []
    lines.append(f"run_id: {run_record.run_id}")
    lines.append(f"started_at: {run_record.started_at}")
    lines.append(f"ended_at: {run_record.ended_at or '-'}")
    lines.append(f"cwd: {run_record.cwd}")
    lines.append(f"status: {run_record.status}")
    lines.append(f"duration_s: {run_record.duration_s if run_record.duration_s is not None else '-'}")
    lines.append(f"exit_code: {run_record.exit_code if run_record.exit_code is not None else '-'}")
    lines.append(f"signal: {run_record.signal if run_record.signal is not None else '-'}")
    lines.append(f"signature_hash: {run_record.signature_hash}")
    lines.append(f"artifacts_dir: {artifact_dir}")
    lines.append("")
    lines.append(f"cmd: {command_argv}")
    lines.append("")
    lines.append("host_facts:")
    for key in sorted(host_facts.keys()):
        lines.append(f"  {key}: {host_facts[key]}")
    return "\n".join(lines)
