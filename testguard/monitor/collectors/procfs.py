from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Set

from testguard.monitor.collectors.base import CollectorAdapter, SampleFragment, Target


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _parse_proc_stat(stat_text: str) -> Dict[str, float]:
    # /proc/<pid>/stat has (comm) which may contain spaces, so split carefully.
    # Format: pid (comm) state ppid ... utime stime ... rss ...
    left_paren = stat_text.find("(")
    right_paren = stat_text.rfind(")")
    if left_paren == -1 or right_paren == -1 or right_paren < left_paren:
        raise ValueError("unexpected /proc stat format")

    after = stat_text[right_paren + 2 :].strip()  # skip ") "
    fields = after.split()

    # These are 1-based positions in man proc, relative to full stat.
    # After splitting off pid and comm, "state" becomes fields[0].
    # utime is field 14, stime 15, rss 24 in full stat.
    # With pid+comm removed, indices shift by -2.
    utime_ticks = int(fields[11])
    stime_ticks = int(fields[12])
    rss_pages = int(fields[21])

    clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    page_size = os.sysconf("SC_PAGE_SIZE")

    return {
        "cpu_user_s": float(utime_ticks) / float(clock_ticks),
        "cpu_sys_s": float(stime_ticks) / float(clock_ticks),
        "rss_bytes_stat": float(rss_pages) * float(page_size),
    }


def _parse_proc_status(status_text: str) -> Dict[str, float]:
    rss_kib = None
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                rss_kib = int(parts[1])
            break

    if rss_kib is None:
        return {}
    return {"rss_bytes": float(rss_kib) * 1024.0}


def _parse_proc_io(io_text: str) -> Dict[str, float]:
    read_bytes = None
    write_bytes = None
    for line in io_text.splitlines():
        if line.startswith("read_bytes:"):
            read_bytes = int(line.split(":")[1].strip())
        elif line.startswith("write_bytes:"):
            write_bytes = int(line.split(":")[1].strip())

    values: Dict[str, float] = {}
    if read_bytes is not None:
        values["io_read_bytes_total"] = float(read_bytes)
    if write_bytes is not None:
        values["io_write_bytes_total"] = float(write_bytes)
    return values


@dataclass
class ProcfsCollector:
    name: str = "procfs"

    def capabilities(self) -> Set[str]:
        return {"cpu_user_s", "cpu_sys_s", "rss_bytes", "io_read_bytes_total", "io_write_bytes_total"}

    def overhead_hint(self) -> str:
        return "reads /proc/<pid>/{stat,status,io} once per tick"

    def sample(self, target: Target) -> SampleFragment:
        pid = target.root_pid
        stat_path = f"/proc/{pid}/stat"
        status_path = f"/proc/{pid}/status"
        io_path = f"/proc/{pid}/io"

        stat_values = _parse_proc_stat(_read_text(stat_path))
        status_values = _parse_proc_status(_read_text(status_path))
        io_values = _parse_proc_io(_read_text(io_path))

        values: Dict[str, float] = {}
        values.update(stat_values)
        values.update(status_values)
        values.update(io_values)

        # Prefer VmRSS if available, else fall back to rss from stat.
        if "rss_bytes" not in values and "rss_bytes_stat" in values:
            values["rss_bytes"] = values["rss_bytes_stat"]
        values.pop("rss_bytes_stat", None)

        return SampleFragment(values=values)
