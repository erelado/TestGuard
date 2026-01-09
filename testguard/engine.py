from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from testguard.controller import SubprocessGroupController
from testguard.logger import get_logger
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import RunMeta
from testguard.util import ensure_dirs, host_facts, make_run_id, now_utc_iso, runs_dir, signature_hash


class Engine:
    def __init__(self, store: SQLiteRunStore) -> None:
        self._store = store
        self._controller = SubprocessGroupController()

    def run_command(self, command_argv: List[str], cwd: str, env: Dict[str, str]) -> str:
        ensure_dirs()
        self._store.init()

        run_id = make_run_id()
        logger = get_logger(run_id=run_id)

        started_at = now_utc_iso()
        run_directory = runs_dir() / run_id
        run_directory.mkdir(parents=True, exist_ok=False)

        facts = host_facts()
        meta = RunMeta(
            run_id=run_id,
            started_at=started_at,
            command_argv=command_argv,
            cwd=cwd,
            signature_hash=signature_hash(command_argv, cwd),
            host_facts_json=json.dumps(facts.__dict__, separators=(",", ":")),
        )
        self._store.create_run(meta)
        logger.info("spawn command: %s", command_argv)

        start_monotonic = time.monotonic()
        handle = self._controller.spawn(argv=command_argv, cwd=cwd, env=env)

        stdout_bytes, stderr_bytes = handle.popen.communicate()
        (run_directory / "stdout.txt").write_bytes(stdout_bytes[:1024 * 1024])
        (run_directory / "stderr.txt").write_bytes(stderr_bytes[:1024 * 1024])

        duration_s = time.monotonic() - start_monotonic
        ended_at = now_utc_iso()

        return_code = handle.popen.returncode
        exit_code: Optional[int] = None
        signal_number: Optional[int] = None
        status = "OK"

        if return_code is None:
            status = "UNKNOWN"
        elif return_code < 0:
            status = "FAILED"
            signal_number = -return_code
        else:
            exit_code = return_code
            status = "OK" if return_code == 0 else "FAILED"

        self._store.finalize_run(
            run_id,
            ended_at=ended_at,
            status=status,
            exit_code=exit_code,
            signal=signal_number,
            duration_s=duration_s,
            notes=None,
        )
        logger.info("completed status=%s exit_code=%s signal=%s duration_s=%.3f", status, exit_code, signal_number, duration_s)
        return run_id
