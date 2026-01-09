from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


def is_linux() -> bool:
    return sys.platform == "linux"


def require_linux(feature_name: str) -> None:
    if not is_linux():
        raise RuntimeError(f"{feature_name} is currently Linux-only.")


def base_dir() -> Path:
    return Path.home() / ".testguard"


def runs_dir() -> Path:
    return base_dir() / "runs"


def db_path() -> Path:
    return base_dir() / "runs.db"


def ensure_dirs() -> None:
    base_dir().mkdir(parents=True, exist_ok=True)
    runs_dir().mkdir(parents=True, exist_ok=True)


def now_utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def make_run_id() -> str:
    timestamp_local = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    suffix = secrets.token_hex(8)
    return f"{timestamp_local}_{suffix}"


def signature_hash(command_argv: List[str], cwd: str) -> str:
    payload = {"argv": command_argv, "cwd": cwd}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass(frozen=True)
class HostFacts:
    os: str
    kernel: str
    machine: str
    cpu_count: int
    python: str
    cgroup_v2_mount: Optional[str]


def detect_cgroup_v2_mount() -> Optional[str]:
    if not is_linux():
        return None
    mount_path = "/sys/fs/cgroup"
    if not os.path.isdir(mount_path):
        return None
    if os.path.exists(os.path.join(mount_path, "cgroup.controllers")):
        return mount_path
    return None


def host_facts() -> HostFacts:
    return HostFacts(
        os=sys.platform,
        kernel=platform.release(),
        machine=platform.machine(),
        cpu_count=os.cpu_count() or 0,
        python=sys.version.split()[0],
        cgroup_v2_mount=detect_cgroup_v2_mount(),
    )
