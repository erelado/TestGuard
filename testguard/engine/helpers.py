from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from testguard.controller import ProcessHandle, SubprocessGroupController
from testguard.governor import Governor, Thresholds
from testguard.monitor.monitor_loop import MonitorLoop
from testguard.store.store import EventRecord, RunMeta
from testguard.engine.tail_buffer import TailBuffer
from testguard.util import host_facts, signature_hash


@dataclass(frozen=True, kw_only=True)
class RunOptions:
    # Sampling
    sample_interval_seconds: float

    # Memory thresholds (0 disables each threshold)
    warn_memory_mebibytes: int
    max_memory_mebibytes: int

    # Runtime threshold (None disables)
    max_runtime_seconds: Optional[float]

    # Disk write threshold (None disables)
    disk_write_mebibytes_per_second: Optional[float]
    disk_write_sustain_seconds: float

    # Process termination behavior
    kill_grace_seconds: float

    # Output capture
    stdout_tail_kibibytes: int
    stderr_tail_kibibytes: int

    # Signature / grouping overrides
    signature_label: Optional[str] = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class OutputCapture:
    stdout_path: Path
    stderr_path: Path
    stdout_tail: TailBuffer
    stderr_tail: TailBuffer
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread

    def join(self, timeout_seconds: float) -> None:
        self.stdout_thread.join(timeout=timeout_seconds)
        self.stderr_thread.join(timeout=timeout_seconds)


class KillState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._killed_by_policy: Optional[dict[str, object]] = None

    def mark_first_kill(self, *, policy_id: str, message: str) -> bool:
        with self._lock:
            if self._killed_by_policy is not None:
                return False
            self._killed_by_policy = {
                "policy_id": policy_id,
                "message": message,
                "triggered_at_monotonic_seconds": time.monotonic(),
            }
            return True

    def notes_json_or_none(self) -> Optional[str]:
        with self._lock:
            if self._killed_by_policy is None:
                return None
            return json.dumps(self._killed_by_policy, sort_keys=True, separators=(",", ":"))

    def was_killed(self) -> bool:
        with self._lock:
            return self._killed_by_policy is not None


def atomic_write_text(path: Path, text: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_json(path: Path, obj: object) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True))


def stream_pipe_to_file_and_tail(pipe, output_path: Path, tail: TailBuffer) -> None:
    with output_path.open("wb") as file_handle:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                break
            file_handle.write(chunk)
            tail.append(chunk)


def build_run_config_json(run_options: RunOptions) -> str:
    """
    Serialize run configuration and metadata into a stable JSON string.

    This JSON is stored on the run record (runs.run_config_json) and is intended to:
    - Preserve the thresholds/sampling used for the run (reproducibility)
    - Preserve optional metadata used for grouping and reporting (signature_label, tags)
    """
    run_config = {
        "signature_label": run_options.signature_label,
        "tags": dict(run_options.tags),
        "sampling": {
            "sample_interval_seconds": float(run_options.sample_interval_seconds),
        },
        "thresholds": {
            "warn_memory_mebibytes": int(run_options.warn_memory_mebibytes),
            "max_memory_mebibytes": int(run_options.max_memory_mebibytes),
            "max_runtime_seconds": float(
                run_options.max_runtime_seconds) if run_options.max_runtime_seconds is not None else None,
            "disk_write_mebibytes_per_second": (
                float(run_options.disk_write_mebibytes_per_second)
                if run_options.disk_write_mebibytes_per_second is not None
                else None
            ),
            "disk_write_sustain_seconds": float(run_options.disk_write_sustain_seconds),
        },
        "output_capture": {
            "stdout_tail_kibibytes": int(run_options.stdout_tail_kibibytes),
            "stderr_tail_kibibytes": int(run_options.stderr_tail_kibibytes),
        },
        "process_control": {
            "kill_grace_seconds": float(run_options.kill_grace_seconds),
        },
    }
    return json.dumps(run_config, sort_keys=True, separators=(",", ":"))


def create_run_meta(
        *,
        run_id: str,
        started_at: str,
        command_argv: List[str],
        cwd: str,
        run_options: RunOptions,
) -> RunMeta:
    facts = host_facts()
    return RunMeta(
        run_id=run_id,
        started_at=started_at,
        command_argv=command_argv,
        cwd=cwd,
        signature_hash=signature_hash(command_argv, cwd, signature_label=run_options.signature_label),
        host_facts_json=json.dumps(facts.__dict__, sort_keys=True, separators=(",", ":")),
        run_config_json=build_run_config_json(run_options),
    )


def start_output_capture(
        *,
        handle: ProcessHandle,
        run_directory: Path,
        run_id: str,
        run_options: RunOptions,
) -> OutputCapture:
    stdout_path = run_directory / "stdout.log"
    stderr_path = run_directory / "stderr.log"

    stdout_tail = TailBuffer(max_bytes=int(run_options.stdout_tail_kibibytes) * 1024)
    stderr_tail = TailBuffer(max_bytes=int(run_options.stderr_tail_kibibytes) * 1024)

    stdout_thread = threading.Thread(
        target=stream_pipe_to_file_and_tail,
        args=(handle.popen.stdout, stdout_path, stdout_tail),
        name=f"testguard-stdout-{run_id}",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=stream_pipe_to_file_and_tail,
        args=(handle.popen.stderr, stderr_path, stderr_tail),
        name=f"testguard-stderr-{run_id}",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    return OutputCapture(
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        stdout_thread=stdout_thread,
        stderr_thread=stderr_thread,
    )


def build_optional_governor(run_options: RunOptions) -> Optional[Governor]:
    warn_memory_bytes = int(run_options.warn_memory_mebibytes) * 1024 * 1024
    max_memory_bytes = int(run_options.max_memory_mebibytes) * 1024 * 1024

    disk_write_rate_bytes_per_second: Optional[float]
    if run_options.disk_write_mebibytes_per_second is None:
        disk_write_rate_bytes_per_second = None
    else:
        disk_write_rate_bytes_per_second = float(run_options.disk_write_mebibytes_per_second) * 1024.0 * 1024.0

    thresholds = Thresholds(
        warn_rss_bytes=max(0, warn_memory_bytes),
        max_rss_bytes=max(0, max_memory_bytes),
        max_runtime_s=run_options.max_runtime_seconds,
        disk_write_rate_bytes_s=disk_write_rate_bytes_per_second,
        disk_write_sustain_s=run_options.disk_write_sustain_seconds,
    )

    any_policy_enabled = (
            thresholds.warn_rss_bytes > 0
            or thresholds.max_rss_bytes > 0
            or thresholds.max_runtime_s is not None
            or (thresholds.disk_write_rate_bytes_s is not None and thresholds.disk_write_rate_bytes_s > 0)
    )
    if not any_policy_enabled:
        return None

    return Governor(thresholds)


def write_command_not_found_artifact(run_directory: Path, missing_executable: str) -> None:
    hint_lines = [
        f"Command not found: {missing_executable}",
        "Tips:",
        "  - If you use uv, run via: uv run testguard run -- <cmd>",
        "  - For pytest specifically, run via: python -m pytest",
    ]
    (run_directory / "error.txt").write_text("\n".join(hint_lines), encoding="utf-8")


def make_ring_snapshot(monitor_loop: MonitorLoop) -> list[dict[str, object]]:
    return [
        {
            "ts_monotonic": sample.ts_monotonic,
            "ts_wall_epoch": sample.ts_wall_epoch,
            "fragments": sample.fragments,
        }
        for sample in monitor_loop.ring_buffer.snapshot()
    ]


def append_event(store, *, run_id: str, event_type: str, message: str, policy_id: Optional[str]) -> None:
    store.append_event(
        event=EventRecord(
            run_id=run_id,
            ts_monotonic=time.monotonic(),
            event_type=event_type,
            message=message,
            policy_id=policy_id,
        )
    )


def terminate_with_escalation(
        *,
        store,
        controller: SubprocessGroupController,
        handle: ProcessHandle,
        run_id: str,
        policy_id: str,
        kill_grace_seconds: float,
) -> None:
    append_event(
        store,
        run_id=run_id,
        event_type="TERM_SENT",
        message="sent SIGTERM to process group",
        policy_id=policy_id,
    )
    controller.terminate_process_group(handle)

    exited = controller.wait_for_exit(handle, timeout_s=kill_grace_seconds)
    if exited is None:
        append_event(
            store,
            run_id=run_id,
            event_type="KILL_SENT",
            message="sent SIGKILL to process group",
            policy_id=policy_id,
        )
        controller.kill_process_group(handle)
