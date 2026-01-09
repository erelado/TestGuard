from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import List
from typing import Optional

from testguard.analyze import compute_metrics, diff_metrics, no_baseline_diff
from testguard.engine import Engine
from testguard.logger import configure_logging
from testguard.report import render_list, render_report
from testguard.report import write_run_report_artifacts
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.summarize import persist_summary_for_run
from testguard.util import base_dir, db_path, host_facts, is_linux
from testguard.util import runs_dir


@dataclass(frozen=True, kw_only=True)
class RunOptions:
    sample_interval_s: float
    warn_memory_mib: int
    max_memory_mib: int
    max_runtime_s: Optional[float]
    disk_write_mib_s: Optional[float]
    disk_write_sustain_s: float


def build_arg_parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(prog="testguard")
    arg_parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")

    subparsers = arg_parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a command under TestGuard")
    run_parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for the command")
    run_parser.add_argument("argv", nargs=argparse.REMAINDER, help="Command to run, after --")
    run_parser.add_argument(
        "--sample-every-seconds",
        "--sample-interval-s",
        dest="sample_interval_s",
        type=float,
        default=0.5,
        help="How often to sample resource usage, in seconds. Smaller values catch spikes better but add overhead.",
    )
    run_parser.add_argument(
        "--warn-memory-mib",
        "--warn-rss-mib",
        dest="warn_memory_mib",
        type=int,
        default=0,
        help=(
            "Warn (do not stop) if memory usage exceeds this many MiB. "
            "On Linux, prefers cgroup memory.current when available, otherwise uses RSS. "
            "0 disables."
        ),
    )
    run_parser.add_argument(
        "--max-memory-mib",
        "--max-rss-mib",
        dest="max_memory_mib",
        type=int,
        default=0,
        help=(
            "Kill the run if memory usage exceeds this many MiB. "
            "On Linux, prefers cgroup memory.current when available, otherwise uses RSS. "
            "0 disables."
        ),
    )
    run_parser.add_argument(
        "--max-runtime-seconds",
        "--max-runtime-s",
        dest="max_runtime_s",
        type=float,
        default=None,
        help="Kill the run if it runs longer than this many seconds. Omit to disable.",
    )
    run_parser.add_argument(
        "--max-disk-write-mib-per-sec",
        "--disk-write-mib-s",
        dest="disk_write_mib_s",
        type=float,
        default=None,
        help="Kill if disk write rate stays above this many MiB/s for the sustain window. Omit to disable.",
    )
    run_parser.add_argument(
        "--disk-write-sustain-seconds",
        "--disk-write-sustain-s",
        dest="disk_write_sustain_s",
        type=float,
        default=3.0,
        help=(
            "How long (seconds) disk write rate must stay above the threshold before killing. "
            "Only applies if the disk write threshold is set."
        ),
    )
    run_parser.add_argument(
        "--baseline",
        dest="baseline_id",
        default=None,
        help="Optional baseline run_id for diff. If omitted, TestGuard picks the latest OK run with the same signature.",
    )

    list_parser = subparsers.add_parser("list", help="List recent runs")
    list_parser.add_argument("--limit", type=int, default=20, help="Max runs to show")

    report_parser = subparsers.add_parser("report", help="Show a stored run")
    report_parser.add_argument("run_id")
    report_parser.add_argument("--baseline", dest="baseline_id", default=None,
                               help="Optional baseline run_id for diff")

    diff_parser = subparsers.add_parser("diff", help="Diff a run vs a baseline")
    diff_parser.add_argument("run_id")
    diff_parser.add_argument("--baseline", dest="baseline_id", default=None)

    subparsers.add_parser("doctor", help="Show environment and collector availability")

    return arg_parser


def _load_metrics_and_diff(*, store: SQLiteRunStore, run_id: str, baseline_id: Optional[str]) -> tuple:
    run_record = store.load_run(run_id)
    current_samples = store.list_samples(run_id)
    current_metrics = compute_metrics(run_record, current_samples)

    chosen_baseline_id = baseline_id
    if chosen_baseline_id is None:
        chosen_baseline_id = store.find_baseline_run_id(run_record.signature_hash, exclude_run_id=run_id)

    if chosen_baseline_id is None:
        diff_summary = no_baseline_diff(current_metrics)
        return run_record, current_metrics, diff_summary

    baseline_record = store.load_run(chosen_baseline_id)
    baseline_samples = store.list_samples(chosen_baseline_id)
    baseline_metrics = compute_metrics(baseline_record, baseline_samples)

    diff_summary = diff_metrics(current_metrics, baseline_metrics, baseline_run_id=chosen_baseline_id)
    return run_record, current_metrics, diff_summary


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


def cmd_run(argv: List[str], cwd: str, run_options: RunOptions) -> int:
    if not argv:
        print("Usage: testguard run -- <command ...>")
        return 2

    if argv[0] == "--":
        argv = argv[1:]

    if not argv:
        print("Usage: testguard run -- <command ...>")
        return 2

    if not is_linux():
        print("Note: monitoring and safety enforcement are currently Linux-only.")
        print("This step still records run metadata and captures bounded stdout and stderr.")

    store = SQLiteRunStore(db_path())
    engine = Engine(store)

    environment = dict(os.environ)
    run_id = engine.run_command(
        command_argv=argv,
        cwd=cwd,
        env=environment,
        sample_interval_s=run_options.sample_interval_s,
        warn_memory_mib=run_options.warn_memory_mib,
        max_memory_mib=run_options.max_memory_mib,
        max_runtime_s=run_options.max_runtime_s,
        disk_write_mib_s=run_options.disk_write_mib_s,
        disk_write_sustain_s=run_options.disk_write_sustain_s,
    )

    run_record, current_metrics, diff_summary = _load_metrics_and_diff(
        store=store,
        run_id=run_id,
        baseline_id=run_options.baseline_id,
    )

    persist_summary_for_run(store, run_record)
    run_record = store.load_run(run_id)

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
    store = SQLiteRunStore(db_path())
    store.init()
    run_records = store.list_runs(limit=limit)
    print(render_list(run_records))
    return 0


def cmd_report(run_id: str, baseline_id: Optional[str]) -> int:
    store = SQLiteRunStore(db_path())
    store.init()

    run_record, current_metrics, diff_summary = _load_metrics_and_diff(
        store=store,
        run_id=run_id,
        baseline_id=baseline_id,
    )

    write_run_report_artifacts(
        run_record=run_record,
        current_metrics=current_metrics,
        diff_summary=diff_summary,
    )

    print(render_report(run_record, current_metrics=current_metrics, diff_summary=diff_summary))
    print(f"artifacts_dir: {runs_dir() / run_id}")
    return 0


def cmd_diff(run_id: str, baseline_id: Optional[str]) -> int:
    store = SQLiteRunStore(db_path())
    store.init()

    run_record = store.load_run(run_id)
    current_samples = store.list_samples(run_id)
    current_metrics = compute_metrics(run_record, current_samples)

    chosen_baseline_id = baseline_id
    if chosen_baseline_id is None:
        chosen_baseline_id = store.find_baseline_run_id(run_record.signature_hash, exclude_run_id=run_id)

    if chosen_baseline_id is None:
        diff_summary = no_baseline_diff(current_metrics)
        write_run_report_artifacts(run_record=run_record, current_metrics=current_metrics, diff_summary=diff_summary)
        print(f"No baseline found for signature_hash={run_record.signature_hash}")
        print(f"Wrote artifacts to: {runs_dir() / run_id}")
        return 0

    baseline_record = store.load_run(chosen_baseline_id)
    baseline_samples = store.list_samples(chosen_baseline_id)
    baseline_metrics = compute_metrics(baseline_record, baseline_samples)

    diff_summary = diff_metrics(current_metrics, baseline_metrics, baseline_run_id=chosen_baseline_id)

    write_run_report_artifacts(run_record=run_record, current_metrics=current_metrics, diff_summary=diff_summary)

    print(f"run_id: {run_record.run_id}")
    print(f"baseline: {chosen_baseline_id}")
    print(f"classification: {diff_summary.classification}")
    print("recommendations:")
    if not diff_summary.recommendations:
        print("  - none")
    else:
        for rec in diff_summary.recommendations:
            print(f"  - {rec['area']}: {rec['message']} (confidence: {rec['confidence']})")
    print(f"artifacts_dir: {runs_dir() / run_id}")
    return 0 if diff_summary.classification != "REGRESSION" else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.log_level)

    if args.command == "doctor":
        return cmd_doctor()

    if args.command == "run":
        run_options = RunOptions(
            sample_interval_s=args.sample_interval_s,
            warn_memory_mib=args.warn_memory_mib,
            max_memory_mib=args.max_memory_mib,
            max_runtime_s=args.max_runtime_s,
            disk_write_mib_s=args.disk_write_mib_s,
            disk_write_sustain_s=args.disk_write_sustain_s,
        )
        return cmd_run(args.argv, args.cwd, run_options)

    if args.command == "list":
        return cmd_list(args.limit)

    if args.command == "report":
        return cmd_report(args.run_id, args.baseline_id)

    if args.command == "diff":
        return cmd_diff(args.run_id, args.baseline_id)

    print("Unknown command")
    return 2


def entrypoint() -> None:
    raise SystemExit(main())
