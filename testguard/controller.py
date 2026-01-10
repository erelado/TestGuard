from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Dict, List
from typing import Optional


@dataclass(frozen=True, kw_only=True)
class ExitResult:
    exit_code: Optional[int]
    signal: Optional[int]


@dataclass
class ProcessHandle:
    popen: subprocess.Popen[bytes]


class SubprocessGroupController:
    """
    Subprocess lifecycle control for a command and all of its descendants

    This module spawns a child process in its own process group (via `start_new_session=True`), then provides helpers
    to terminate or kill the entire group. That matters because test commands often spawn additional processes,
    and we want "stop" to apply to the whole tree, not only the parent process
    """
    def spawn(self, argv: List[str], cwd: str, env: Dict[str, str]) -> ProcessHandle:
        """starts the command with stdout/stderr captured (bytes), isolated in a new session"""
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return ProcessHandle(popen=process)

    def terminate_process_group(self, handle: ProcessHandle) -> None:
        """sends SIGTERM to the process group (polite shutdown)"""
        process_group_id = os.getpgid(handle.popen.pid)
        os.killpg(process_group_id, signal.SIGTERM)

    def kill_process_group(self, handle: ProcessHandle) -> None:
        """sends SIGKILL to the process group (forceful stop)"""
        process_group_id = os.getpgid(handle.popen.pid)
        os.killpg(process_group_id, signal.SIGKILL)

    def wait_for_exit(self, handle: ProcessHandle, timeout_s: Optional[float]) -> Optional[int]:
        """waits for completion up to an optional timeout, returning the exit code or None if the timeout elapsed"""
        try:
            return handle.popen.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None
