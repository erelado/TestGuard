from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from testguard.monitor.collectors.base import CollectorAdapter, Target


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _is_cgroup_v2_mount(cgroup_root: Path) -> bool:
    return (cgroup_root / "cgroup.controllers").exists() and (cgroup_root / "cgroup.procs").exists()


def _parse_proc_cgroup(proc_cgroup_text: str) -> Optional[str]:
    # cgroup v2 line format: "0::/some/path"
    for line in proc_cgroup_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":", maxsplit=2)
        if len(parts) != 3:
            continue
        hierarchy_id, controllers, path = parts
        if hierarchy_id == "0" and controllers == "":
            return path
    return None


def _parse_memory_max_bytes(text: str) -> Optional[int]:
    value = text.strip()
    if value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_cpu_stat_seconds(cpu_stat_text: str) -> Tuple[Optional[float], Optional[float]]:
    # cpu.stat keys: usage_usec, user_usec, system_usec, ...
    user_usec: Optional[int] = None
    system_usec: Optional[int] = None

    for line in cpu_stat_text.splitlines():
        line = line.strip()
        if not line:
            continue
        key, value = line.split(maxsplit=1)
        if key == "user_usec":
            user_usec = int(value)
        elif key == "system_usec":
            system_usec = int(value)

    user_s = None if user_usec is None else user_usec / 1_000_000.0
    system_s = None if system_usec is None else system_usec / 1_000_000.0
    return user_s, system_s


def _parse_io_stat_totals(io_stat_text: str) -> Tuple[Optional[int], Optional[int]]:
    # io.stat lines look like:
    # 8:0 rbytes=123 wbytes=456 rios=... wios=...
    total_rbytes = 0
    total_wbytes = 0
    any_found = False

    for line in io_stat_text.splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = line.split()
        for token in tokens[1:]:
            if token.startswith("rbytes="):
                total_rbytes += int(token.split("=", 1)[1])
                any_found = True
            elif token.startswith("wbytes="):
                total_wbytes += int(token.split("=", 1)[1])
                any_found = True

    if not any_found:
        return None, None
    return total_rbytes, total_wbytes


def _try_create_run_cgroup(*, cgroup_root: Path, run_id: str, pid: int) -> Optional[Path]:
    # Best-effort: create /sys/fs/cgroup/testguard/<run_id>/ and move pid into it.
    run_dir = cgroup_root / "testguard" / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "cgroup.procs").write_text(f"{pid}\n", encoding="utf-8")
        return run_dir
    except Exception:
        return None


@dataclass(frozen=True, kw_only=True)
class CgroupV2Paths:
    cgroup_root: Path
    run_cgroup_dir: Optional[Path]
    proc_root: Path


class CgroupV2Collector(CollectorAdapter):
    name = "cgroupv2"

    def __init__(self, *, paths: CgroupV2Paths) -> None:
        self._paths = paths

    @classmethod
    def for_run(
        cls,
        *,
        run_id: str,
        pid: int,
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        proc_root: Path = Path("/proc"),
    ) -> Optional["CgroupV2Collector"]:
        if not _is_cgroup_v2_mount(cgroup_root):
            return None

        run_cgroup_dir = _try_create_run_cgroup(cgroup_root=cgroup_root, run_id=run_id, pid=pid)
        return cls(paths=CgroupV2Paths(cgroup_root=cgroup_root, run_cgroup_dir=run_cgroup_dir, proc_root=proc_root))

    def capabilities(self) -> set[str]:
        return {
            "cgroup_memory_current_bytes",
            "cgroup_memory_max_bytes",
            "cpu_user_s",
            "cpu_sys_s",
            "io_read_bytes_total",
            "io_write_bytes_total",
            "pids_current",
        }

    def overhead_hint(self) -> str:
        return "Reads cgroup v2 cpu.stat, memory.current, memory.max, io.stat, pids.current once per tick."

    def _resolve_cgroup_dir(self, pid: int) -> Optional[Path]:
        if self._paths.run_cgroup_dir is not None:
            return self._paths.run_cgroup_dir

        proc_cgroup_path = self._paths.proc_root / str(pid) / "cgroup"
        try:
            cgroup_rel = _parse_proc_cgroup(_read_text(proc_cgroup_path))
        except FileNotFoundError:
            return None

        if cgroup_rel is None:
            return None

        # cgroup_rel starts with "/", join carefully
        return self._paths.cgroup_root / cgroup_rel.lstrip("/")

    def sample(self, target: Target) -> Dict[str, object]:
        pid = int(target.root_pid)
        cgroup_dir = self._resolve_cgroup_dir(pid)
        if cgroup_dir is None:
            return {}

        fragments: Dict[str, object] = {}

        try:
            fragments["cgroup_memory_current_bytes"] = int(_read_text(cgroup_dir / "memory.current"))
        except Exception:
            pass

        try:
            memory_max = _parse_memory_max_bytes(_read_text(cgroup_dir / "memory.max"))
            if memory_max is not None:
                fragments["cgroup_memory_max_bytes"] = memory_max
        except Exception:
            pass

        try:
            cpu_user_s, cpu_sys_s = _parse_cpu_stat_seconds(_read_text(cgroup_dir / "cpu.stat"))
            if cpu_user_s is not None:
                fragments["cpu_user_s"] = cpu_user_s
            if cpu_sys_s is not None:
                fragments["cpu_sys_s"] = cpu_sys_s
        except Exception:
            pass

        try:
            read_bytes, write_bytes = _parse_io_stat_totals(_read_text(cgroup_dir / "io.stat"))
            if read_bytes is not None:
                fragments["io_read_bytes_total"] = read_bytes
            if write_bytes is not None:
                fragments["io_write_bytes_total"] = write_bytes
        except Exception:
            pass

        try:
            fragments["pids_current"] = int(_read_text(cgroup_dir / "pids.current"))
        except Exception:
            pass

        return fragments
