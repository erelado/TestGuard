from __future__ import annotations

import os
from typing import Optional

from testguard.cli.arg_parser import RunOptions, normalize_command_argv
from testguard.cli.helpers import load_metrics_and_diff
from testguard.engine import Engine
from testguard.report import render_list, render_report, write_run_report_artifacts
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.summary import persist_summary_for_run
from testguard.util import base_dir, db_path, host_facts, is_linux, runs_dir


def _parse_tag_items(tag_items: list[str]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in tag_items:
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Invalid tag (expected key=value): {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Invalid tag key: {item!r}")
        tags[key] = value
    return tags


def _enrich_default_tags(*, tags: dict[str, str]) -> dict[str, str]:
    """
    Add a few stable defaults, only when the user did not set them.
    These defaults help future remote baseline filtering.
    """
    facts = host_facts()

    if "os" not in tags:
        tags["os"] = str(facts.os)

    if "python" not in tags:
        tags["python"] = str(facts.python)

    return tags


def cmd_doctor() -> int:
    facts = host_facts()
    print("TestGuard doctor")
    print(f"os: {facts.os}")
    print(f"base_dir: {base_dir()}")
    print(f"db_path: {db_path()}")
    print(f"kernel: {facts.kernel}")
    print(f"machine: {facts.machine}")
    print(f"cpu_count: {facts.cpu_count}")
    print(f"python: {facts.python}")
    print(f"cgroup_v2_mount: {facts.cgroup_v2_mount or 'none'}")
    print("monitoring: currently Linux-only")
    print("planned collectors: procfs, cgroupv2, psi (diagnostic), gpu (optional)")
    return 0


def cmd_run(argv: list[str], cwd: str, run_options: RunOptions) -> int:
    command_argv = normalize_command_argv(argv)
    if not command_argv:
        print("Usage: testguard run -- <command ...>")
        return 2

    if not is_linux():
        print("Note: monitoring and safety enforcement are currently Linux-only.")
        print("This step still records run metadata and captures bounded stdout and stderr.")

    store = SQLiteRunStore(database_path=db_path())
    store.init()

    engine = Engine(store)
    environment = dict(os.environ)

    tags = _parse_tag_items(run_options.tag_items)
    tags = _enrich_default_tags(tags=tags)

    run_id = engine.run_command(
        command_argv=command_argv,
        cwd=cwd,
        env=environment,
        sample_interval_s=run_options.sample_interval_seconds,
        warn_memory_mib=run_options.warn_memory_mebibytes,
        max_memory_mib=run_options.max_memory_mebibytes,
        max_runtime_s=run_options.max_runtime_seconds,
        disk_write_mib_s=run_options.disk_write_mebibytes_per_second,
        disk_write_sustain_s=run_options.disk_write_sustain_seconds,
        signature_label=run_options.signature_label,
        tags=tags,
    )

    run_record, current_metrics, diff_summary = load_metrics_and_diff(
        store=store,
        run_id=run_id,
        baseline_run_id=run_options.baseline_run_id,
    )

    persist_summary_for_run(store=store, run_record=run_record)
    run_record = store.load_run(run_id=run_id)

    write_run_report_artifacts(
        run_record=run_record,
        current_metrics=current_metrics,
        diff_summary=diff_summary,
    )

    print(render_report(run_record, current_metrics=current_metrics, diff_summary=diff_summary))
    print(f"artifacts_dir: {runs_dir() / run_id}")

    if run_record.status != "OK":
        return 1
    if diff_summary.classification == "REGRESSION":
        return 1
    return 0


def cmd_list(limit: int) -> int:
    store = SQLiteRunStore(database_path=db_path())
    store.init()
    run_records = store.list_runs(limit=limit)
    print(render_list(run_records))
    return 0


def cmd_report(run_id: str, baseline_run_id: Optional[str]) -> int:
    store = SQLiteRunStore(database_path=db_path())
    store.init()

    run_record, current_metrics, diff_summary = load_metrics_and_diff(
        store=store,
        run_id=run_id,
        baseline_run_id=baseline_run_id,
    )

    write_run_report_artifacts(
        run_record=run_record,
        current_metrics=current_metrics,
        diff_summary=diff_summary,
    )

    print(render_report(run_record, current_metrics=current_metrics, diff_summary=diff_summary))
    print(f"artifacts_dir: {runs_dir() / run_id}")
    return 0


def cmd_diff(run_id: str, baseline_run_id: Optional[str]) -> int:
    store = SQLiteRunStore(database_path=db_path())
    store.init()

    run_record, current_metrics, diff_summary = load_metrics_and_diff(
        store=store,
        run_id=run_id,
        baseline_run_id=baseline_run_id,
    )

    write_run_report_artifacts(
        run_record=run_record,
        current_metrics=current_metrics,
        diff_summary=diff_summary,
    )

    print(f"run_id: {run_record.run_id}")
    print(f"baseline: {diff_summary.baseline_run_id or '-'}")
    print(f"classification: {diff_summary.classification}")
    print("recommendations:")
    if not diff_summary.recommendations:
        print("  - none")
    else:
        for rec in diff_summary.recommendations:
            print(f"  - {rec['area']}: {rec['message']} (confidence: {rec['confidence']})")
    print(f"artifacts_dir: {runs_dir() / run_id}")

    return 0 if diff_summary.classification != "REGRESSION" else 1
