from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from testguard.monitor.collectors.base import CollectorAdapter, Target


@dataclass(frozen=True, kw_only=True)
class ProcfsSample:
    rss_bytes: Optional[int]
    cpu_user_s: Optional[float]
    cpu_sys_s: Optional[float]
    io_read_bytes_total: Optional[int]
    io_write_bytes_total: Optional[int]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_status_rss_bytes(status_text: str) -> Optional[int]:
    # VmRSS:        12345 kB
    for line in status_text.splitlines():
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return None
        value_kib = int(parts[1])
        return value_kib * 1024
    return None


def _parse_io_totals(io_text: str) -> tuple[Optional[int], Optional[int]]:
    # Prefer read_bytes/write_bytes, they represent actual disk IO.
    read_bytes = None
    write_bytes = None
    for line in io_text.splitlines():
        if line.startswith("read_bytes:"):
            read_bytes = int(line.split()[1])
        elif line.startswith("write_bytes:"):
            write_bytes = int(line.split()[1])
    return read_bytes, write_bytes


def _parse_stat_cpu_seconds(stat_text: str, *, clock_ticks_per_second: int) -> tuple[Optional[float], Optional[float]]:
    # /proc/<pid>/stat has comm in parentheses that may contain spaces.
    # We locate the last ')' and split the remainder.
    right_paren_index = stat_text.rfind(")")
    if right_paren_index == -1:
        return None, None

    after = stat_text[right_paren_index + 1 :].strip()
    fields = after.split()

    # Fields are 1-indexed in procfs docs. After ')' we are at field 3 (state).
    # We want utime (14) and stime (15), so indices in `fields` are:
    # utime: 14 - 3 = 11
    # stime: 15 - 3 = 12
    utime_index = 11
    stime_index = 12
    if len(fields) <= stime_index:
        return None, None

    user_ticks = int(fields[utime_index])
    sys_ticks = int(fields[stime_index])

    user_s = user_ticks / float(clock_ticks_per_second)
    sys_s = sys_ticks / float(clock_ticks_per_second)
    return user_s, sys_s


class ProcfsCollector(CollectorAdapter):
    name = "procfs"

    def __init__(self, *, proc_root: Path = Path("/proc")) -> None:
        self._proc_root = proc_root
        self._clock_ticks_per_second = int(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))

    def capabilities(self) -> set[str]:
        return {"rss_bytes", "cpu_user_s", "cpu_sys_s", "io_read_bytes_total", "io_write_bytes_total"}

    def overhead_hint(self) -> str:
        return "Reads /proc/<pid>/status, /proc/<pid>/stat, /proc/<pid>/io once per tick."

    def sample(self, target: Target) -> Dict[str, object]:
        pid = int(target.root_pid)
        base = self._proc_root / str(pid)

        rss_bytes = None
        cpu_user_s = None
        cpu_sys_s = None
        io_read_bytes_total = None
        io_write_bytes_total = None

        try:
            status_text = _read_text(base / "status")
            rss_bytes = _parse_status_rss_bytes(status_text)
        except FileNotFoundError:
            pass

        try:
            stat_text = _read_text(base / "stat")
            cpu_user_s, cpu_sys_s = _parse_stat_cpu_seconds(
                stat_text,
                clock_ticks_per_second=self._clock_ticks_per_second,
            )
        except FileNotFoundError:
            pass

        try:
            io_text = _read_text(base / "io")
            io_read_bytes_total, io_write_bytes_total = _parse_io_totals(io_text)
        except FileNotFoundError:
            pass

        fragments: Dict[str, object] = {}
        if rss_bytes is not None:
            fragments["rss_bytes"] = rss_bytes
        if cpu_user_s is not None:
            fragments["cpu_user_s"] = cpu_user_s
        if cpu_sys_s is not None:
            fragments["cpu_sys_s"] = cpu_sys_s
        if io_read_bytes_total is not None:
            fragments["io_read_bytes_total"] = io_read_bytes_total
        if io_write_bytes_total is not None:
            fragments["io_write_bytes_total"] = io_write_bytes_total

        return fragments
