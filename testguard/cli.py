from __future__ import annotations

import argparse
import json
import os
from typing import List, Optional

from testguard.engine import Engine
from testguard.logger import configure_logging
from testguard.report import render_list, render_report
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.util import base_dir, db_path, host_facts, is_linux


def build_arg_parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(prog="testguard")
    arg_parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")

    subparsers = arg_parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a command under TestGuard")
    run_parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for the command")
    run_parser.add_argument("argv", nargs=argparse.REMAINDER, help="Command to run, after --")

    list_parser = subparsers.add_parser("list", help="List recent runs")
    list_parser.add_argument("--limit", type=int, default=20, help="Max runs to show")

    report_parser = subparsers.add_parser("report", help="Show a stored run")
    report_parser.add_argument("run_id")

    diff_parser = subparsers.add_parser("diff", help="Diff a run vs a baseline (later step)")
    diff_parser.add_argument("run_id")
    diff_parser.add_argument("--baseline", dest="baseline_id", default=None)

    subparsers.add_parser("doctor", help="Show environment and collector availability")

    return arg_parser


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


def cmd_run(argv: List[str], cwd: str) -> int:
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
    run_id = engine.run_command(command_argv=argv, cwd=cwd, env=environment)

    run_record = store.load_run(run_id)
    print(f"run_id: {run_record.run_id}")
    print(f"cmd: {json.loads(run_record.command_argv_json)}")
    print(f"status: {run_record.status}")
    print(f"duration_s: {run_record.duration_s}")
    print(f"exit_code: {run_record.exit_code}  signal: {run_record.signal}")
    return 0 if run_record.status == "OK" else 1


def cmd_list(limit: int) -> int:
    store = SQLiteRunStore(db_path())
    store.init()
    run_records = store.list_runs(limit=limit)
    print(render_list(run_records))
    return 0


def cmd_report(run_id: str) -> int:
    store = SQLiteRunStore(db_path())
    store.init()
    run_record = store.load_run(run_id)
    print(render_report(run_record))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.log_level)

    if args.command == "doctor":
        return cmd_doctor()

    if args.command == "run":
        return cmd_run(args.argv, args.cwd)

    if args.command == "list":
        return cmd_list(args.limit)

    if args.command == "report":
        return cmd_report(args.run_id)

    if args.command == "diff":
        print("Not implemented in this step.")
        return 2

    print("Unknown command")
    return 2


def entrypoint() -> None:
    raise SystemExit(main())
