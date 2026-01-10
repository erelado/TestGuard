from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, kw_only=True)
class RunOptions:
    sample_interval_seconds: float
    warn_memory_mebibytes: int
    max_memory_mebibytes: int
    max_runtime_seconds: Optional[float]
    disk_write_mebibytes_per_second: Optional[float]
    disk_write_sustain_seconds: float
    baseline_run_id: Optional[str]


def build_arg_parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(prog="testguard")
    arg_parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")

    subparsers = arg_parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a command under TestGuard")
    run_parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for the command")
    run_parser.add_argument("argv", nargs=argparse.REMAINDER, help="Command to run, after --")
    run_parser.add_argument(
        "--sample-interval-seconds",
        "--sample-every-seconds",
        "--sample-interval-s",
        dest="sample_interval_seconds",
        type=float,
        default=0.5,
        help="How often to sample resource usage, in seconds. Smaller values catch spikes better but add overhead.",
    )
    run_parser.add_argument(
        "--warn-memory-mib",
        "--warn-rss-mib",
        dest="warn_memory_mebibytes",
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
        dest="max_memory_mebibytes",
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
        dest="max_runtime_seconds",
        type=float,
        default=None,
        help="Kill the run if it runs longer than this many seconds. Omit to disable.",
    )
    run_parser.add_argument(
        "--max-disk-write-mib-per-second",
        "--max-disk-write-mib-per-sec",
        "--disk-write-mib-s",
        dest="disk_write_mebibytes_per_second",
        type=float,
        default=None,
        help="Kill if disk write rate stays above this many MiB/s for the sustain window. Omit to disable.",
    )
    run_parser.add_argument(
        "--disk-write-sustain-seconds",
        "--disk-write-sustain-s",
        dest="disk_write_sustain_seconds",
        type=float,
        default=3.0,
        help=(
            "How long (seconds) disk write rate must stay above the threshold before killing. "
            "Only applies if the disk write threshold is set."
        ),
    )
    run_parser.add_argument(
        "--baseline",
        dest="baseline_run_id",
        default=None,
        help="Optional baseline run_id for diff. If omitted, TestGuard picks the latest OK run with the same signature.",
    )

    list_parser = subparsers.add_parser("list", help="List recent runs")
    list_parser.add_argument("--limit", type=int, default=20, help="Max runs to show")

    report_parser = subparsers.add_parser("report", help="Show a stored run")
    report_parser.add_argument("run_id")
    report_parser.add_argument("--baseline", dest="baseline_run_id", default=None, help="Optional baseline run_id for diff")

    diff_parser = subparsers.add_parser("diff", help="Diff a run vs a baseline")
    diff_parser.add_argument("run_id")
    diff_parser.add_argument("--baseline", dest="baseline_run_id", default=None)

    subparsers.add_parser("doctor", help="Show environment and collector availability")

    return arg_parser


def normalize_command_argv(argv: list[str]) -> list[str]:
    if not argv:
        return []
    if argv[0] == "--":
        return argv[1:]
    return argv
