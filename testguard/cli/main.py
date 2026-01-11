from __future__ import annotations

from typing import Optional

from testguard.cli.arg_parser import RunOptions, build_arg_parser
from testguard.cli.commands import cmd_diff, cmd_doctor, cmd_list, cmd_report, cmd_run
from testguard.logger import configure_logging


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(args.log_level)

    if args.command == "doctor":
        return cmd_doctor()

    if args.command == "run":
        run_options = RunOptions(
            sample_interval_seconds=args.sample_interval_seconds,
            warn_memory_mebibytes=args.warn_memory_mebibytes,
            max_memory_mebibytes=args.max_memory_mebibytes,
            max_runtime_seconds=args.max_runtime_seconds,
            disk_write_mebibytes_per_second=args.disk_write_mebibytes_per_second,
            disk_write_sustain_seconds=args.disk_write_sustain_seconds,
            baseline_run_id=args.baseline_run_id,
            signature_label="default",
            tag_items=[],
        )
        return cmd_run(args.argv, args.cwd, run_options)

    if args.command == "list":
        return cmd_list(args.limit)

    if args.command == "report":
        return cmd_report(args.run_id, args.baseline_run_id)

    if args.command == "diff":
        return cmd_diff(args.run_id, args.baseline_run_id)

    print("Unknown command")
    return 2


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
