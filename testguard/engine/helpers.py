from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from testguard.controller import ProcessHandle, SubprocessGroupController
from testguard.governor import Governor, Thresholds
from testguard.monitor.monitor_loop import MonitorLoop
from testguard.store.store import EventRecord, RunMeta
from testguard.tail_buffer import TailBuffer
from testguard.util import host_facts, signature_hash


@dataclass(frozen=True, kw_only=True)
class RunOptions:
    sample_interval_s: float
    warn_rss_mib: int
    max_rss_mib: int
    max_runtime_s: Optional[float]
    disk_write_mib_s: Optional[float]
    disk_write_sustain_s: float
    kill_grace_s: float
    stdout_tail_kib: int
    stderr_tail_kib: int


@dataclass
class OutputCapture:
    stdout_path: Path
    stderr_path: Path
    stdout_tail: TailBuffer
    stderr_tail: TailBuffer
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread

    def join(self, timeout_s: float) -> None:
        self.stdout_thread.join(timeout=timeout_s)
        self.stderr_thread.join(timeout=timeout_s)


class KillState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._killed_by_policy: Optional[dict] = None

    def mark_first_kill(self, *, policy_id: str, message: str) -> bool:
        with self._lock:
            if self._killed_by_policy is not None:
                return False
            self._killed_by_policy = {
                "policy_id": policy_id,
                "message": message,
                "triggered_at_monotonic": time.monotonic(),
            }
            return True

    def notes_json_or_none(self) -> Optional[str]:
        with self._lock:
            if self._killed_by_policy is None:
                return None
            return json.dumps(self._killed_by_policy, separators=(",", ":"))

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
    run_config = {
        "sample_interval_s": run_options.sample_interval_s,
        "warn_rss_mib": run_options.warn_rss_mib,
        "max_rss_mib": run_options.max_rss_mib,
        "max_runtime_s": run_options.max_runtime_s,
        "disk_write_mib_s": run_options.disk_write_mib_s,
        "disk_write_sustain_s": run_options.disk_write_sustain_s,
        "kill_grace_s": run_options.kill_grace_s,
        "stdout_tail_kib": run_options.stdout_tail_kib,
        "stderr_tail_kib": run_options.stderr_tail_kib,
    }
    return json.dumps(run_config, separators=(",", ":"))


def create_run_meta(*, run_id: str, started_at: str, command_argv: List[str], cwd: str,
                    run_options: RunOptions) -> RunMeta:
    facts = host_facts()
    return RunMeta(
        run_id=run_id,
        started_at=started_at,
        command_argv=command_argv,
        cwd=cwd,
        signature_hash=signature_hash(command_argv, cwd),
        host_facts_json=json.dumps(facts.__dict__, separators=(",", ":")),
        run_config_json=build_run_config_json(run_options),
    )


def start_output_capture(*, handle: ProcessHandle, run_directory: Path, run_id: str,
                         run_options: RunOptions) -> OutputCapture:
    stdout_path = run_directory / "stdout.log"
    stderr_path = run_directory / "stderr.log"

    stdout_tail = TailBuffer(max_bytes=run_options.stdout_tail_kib * 1024)
    stderr_tail = TailBuffer(max_bytes=run_options.stderr_tail_kib * 1024)

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
    rss_warn_bytes = run_options.warn_rss_mib * 1024 * 1024
    rss_max_bytes = run_options.max_rss_mib * 1024 * 1024

    if rss_warn_bytes <= 0 or rss_max_bytes <= 0:
        return None

    thresholds = Thresholds(
        warn_rss_bytes=rss_warn_bytes,
        max_rss_bytes=rss_max_bytes,
        max_runtime_s=run_options.max_runtime_s,
        disk_write_rate_bytes_s=(
                run_options.disk_write_mib_s * 1024 * 1024) if run_options.disk_write_mib_s is not None else None,
        disk_write_sustain_s=run_options.disk_write_sustain_s,
    )
    return Governor(thresholds)


def write_command_not_found_artifact(run_directory: Path, missing_executable: str) -> None:
    hint_lines = [
        f"Command not found: {missing_executable}",
        "Tips:",
        "  - If you use uv, run via: uv run testguard run -- <cmd>",
        "  - For pytest specifically, run via: python -m pytest",
    ]
    (run_directory / "error.txt").write_text("\n".join(hint_lines), encoding="utf-8")


def make_ring_snapshot(monitor_loop: MonitorLoop) -> list:
    return [
        {
            "ts_monotonic": sample.ts_monotonic,
            "ts_wall_epoch": sample.ts_wall_epoch,
            "fragments": sample.fragments,
        }
        for sample in monitor_loop.ring_buffer.snapshot()
    ]


def append_event(store, *, run_id: str, event_type: str, message: str, policy_id: Optional[str]) -> None:
    store.append_event(event=EventRecord(
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
        kill_grace_s: float,
) -> None:
    append_event(store, run_id=run_id, event_type="TERM_SENT", message="sent SIGTERM to process group",
                 policy_id=policy_id)
    controller.terminate_process_group(handle)

    exited = controller.wait_for_exit(handle, timeout_s=kill_grace_s)
    if exited is None:
        append_event(store, run_id=run_id, event_type="KILL_SENT", message="sent SIGKILL to process group",
                     policy_id=policy_id)
        controller.kill_process_group(handle)
