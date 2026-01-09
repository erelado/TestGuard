from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional
import time
from typing import Optional


@dataclass(frozen=True)
class ExitResult:
    exit_code: Optional[int]
    signal: Optional[int]


@dataclass
class ProcessHandle:
    popen: subprocess.Popen[bytes]


class SubprocessGroupController:
    def spawn(self, argv: List[str], cwd: str, env: Dict[str, str]) -> ProcessHandle:
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
        process_group_id = os.getpgid(handle.popen.pid)
        os.killpg(process_group_id, signal.SIGTERM)

    def kill_process_group(self, handle: ProcessHandle) -> None:
        process_group_id = os.getpgid(handle.popen.pid)
        os.killpg(process_group_id, signal.SIGKILL)

    def wait_for_exit(self, handle: ProcessHandle, timeout_s: Optional[float]) -> Optional[int]:
        try:
            return handle.popen.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None
