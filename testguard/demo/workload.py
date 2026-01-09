from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Optional

"""
TestGuard demo workload.

This module provides a small, deterministic program that you run *under TestGuard* to validate real end-to-end behavior,
locally and in CI (especially on Linux).

It is not a mock of TestGuard. It is a controllable workload that produces predictable resource usage so TestGuard can 
observe it via real collectors (procfs and or cgroup v2) and enforce real governor policies.

What this workload can do (all optional):
- Allocate memory and hold it in RAM (to raise RSS or cgroup memory.current).
- Write a fixed amount of data to disk, optionally throttled to a target MiB/s
  (to test sustained disk write rate enforcement).
- Sleep for a configurable time so the monitor has time to collect samples and sustained conditions can trigger.

Typical usage (Linux, run via the real CLI):
- Memory kill:
  testguard run --max-memory-mib 64 -- python -m testguard.demo.workload --alloc-mib 256 --hold-s 10

- Timeout kill:
  testguard run --max-runtime-s 1 -- python -m testguard.demo.workload --hold-s 5

- Sustained disk write rate kill:
  testguard run --disk-write-mib-s 2 --disk-write-sustain-s 1.5 -- \
    python -m testguard.demo.workload --write-mib 64 --write-mib-per-s 6 --hold-s 10

Notes:
- Disk writes are fsync'ed to encourage the OS to account the writes promptly, which helps collectors based on /proc 
  and or cgroup io stats.
- The generated output file is written into --write-dir (or the current working directory) and is intentionally 
  a single file for simplicity.
"""

_MIB = 1024 * 1024


def _allocate_memory_mib(target_mib: int, *, chunk_mib: int = 8) -> list[bytearray]:
    """Allocate approximately target_mib of memory in 8 MiB chunks."""
    assert target_mib >= 0
    assert chunk_mib > 0

    allocated: list[bytearray] = []
    chunk_bytes = chunk_mib * _MIB
    allocated_mib = 0

    while allocated_mib < target_mib:
        remaining_mib = target_mib - allocated_mib
        this_chunk_mib = min(chunk_mib, remaining_mib)
        allocated.append(bytearray(this_chunk_mib * _MIB))
        allocated_mib += this_chunk_mib

    return allocated


def _write_disk(
        *,
        directory: Path,
        total_mib: int,
        rate_mib_s: Optional[float],
        block_mib: int = 1,
) -> None:
    """Write total_mib of data to disk, optionally throttled to rate_mib_s."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "testguard_demo_output.bin"

    total_bytes = total_mib * _MIB
    block_bytes = block_mib * _MIB
    pattern = b"x" * block_bytes

    written = 0
    start = time.monotonic()

    with path.open("wb", buffering=0) as fh:
        while written < total_bytes:
            fh.write(pattern)
            written += block_bytes

            # Optional throttle for deterministic sustained IO
            if rate_mib_s and rate_mib_s > 0:
                expected_elapsed = (written / _MIB) / float(rate_mib_s)
                actual_elapsed = time.monotonic() - start
                sleep_s = expected_elapsed - actual_elapsed
                if sleep_s > 0:
                    time.sleep(sleep_s)

            fh.flush()
            os.fsync(fh.fileno())

    try:
        os.sync()
    except Exception:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m testguard.demo.workload")
    parser.add_argument("--alloc-mib", type=int, default=0, help="Allocate this many MiB of RAM and hold it.")
    parser.add_argument("--hold-s", type=float, default=5.0, help="How long to keep process alive after setup.")
    parser.add_argument("--write-mib", type=int, default=0, help="Write this many MiB to disk (single file).")
    parser.add_argument(
        "--write-mib-per-s",
        type=float,
        default=None,
        help="Throttle disk writes to this many MiB/s. Omit for unthrottled writes.",
    )
    parser.add_argument(
        "--write-dir",
        type=str,
        default=None,
        help="Directory for file writes (default: current working directory).",
    )

    args = parser.parse_args(argv)

    # Sanity checks
    if args.alloc_mib < 0 or args.write_mib < 0 or args.hold_s < 0:
        raise ValueError("All numeric arguments must be non-negative.")

    _ = _allocate_memory_mib(args.alloc_mib)

    if args.write_mib > 0:
        directory = Path(args.write_dir or Path.cwd())
        _write_disk(directory=directory, total_mib=args.write_mib, rate_mib_s=args.write_mib_per_s)

    # Keep process alive for sampling
    time.sleep(args.hold_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
