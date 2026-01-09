from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from testguard.store.store import RunRecord
from testguard.util import runs_dir


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def render_list(run_records: List[RunRecord]) -> str:
    lines: List[str] = []
    header = "started_at                  run_id                 status   dur_s   exit"
    lines.append(header)
    lines.append("-" * len(header))

    for record in run_records:
        duration = f"{record.duration_s:.1f}" if record.duration_s is not None else "-"
        exit_part = "-"
        if record.exit_code is not None:
            exit_part = str(record.exit_code)
        elif record.signal is not None:
            exit_part = f"sig:{record.signal}"

        lines.append(
            f"{record.started_at:26} {record.run_id:20} {record.status:7} {duration:6} {exit_part:5}"
        )

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
