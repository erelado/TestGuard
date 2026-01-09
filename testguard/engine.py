from __future__ import annotations

import json
import threading
import time
from typing import Dict, List, Optional

from testguard.controller import SubprocessGroupController
from testguard.logger import get_logger, get_named_logger
from testguard.monitor.collectors.base import Target
from testguard.monitor.collectors.procfs import ProcfsCollector
from testguard.monitor.monitor_loop import MonitorConfig, MonitorLoop
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import EventRecord, RunMeta, SampleRecord
from testguard.util import (
    ensure_dirs,
    host_facts,
    is_linux,
    make_run_id,
    now_utc_iso,
    runs_dir,
    signature_hash,
)


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

        def should_stop() -> bool:
            return handle.popen.poll() is not None

        monitor_thread: Optional[threading.Thread] = None

        if is_linux():
            monitor_logger = get_named_logger("testguard.monitor", run_id=run_id)

            monitor_config = MonitorConfig(
                sample_interval_s=0.5,
                max_consecutive_failures=3,
                ring_buffer_seconds=30.0,
            )
            monitor_loop = MonitorLoop(
                collectors=[ProcfsCollector()],
                target=Target(root_pid=handle.popen.pid),
                config=monitor_config,
                logger=monitor_logger,
            )

            def monitor_worker() -> None:
                batch: List[SampleRecord] = []
                batch_size = 50
                consecutive_failures = 0

                def flush_batch() -> None:
                    if not batch:
                        return
                    self._store.append_samples(run_id, list(batch))
                    batch.clear()

                monitor_logger.info(
                    "monitor started interval_s=%.3f",
                    monitor_config.sample_interval_s,
                )

                next_tick = time.monotonic()

                while True:
                    if should_stop():
                        flush_batch()
                        monitor_logger.info("monitor stopping, process exited")
                        return

                    now = time.monotonic()
                    sleep_s = next_tick - now
                    if sleep_s > 0:
                        time.sleep(sleep_s)

                    if should_stop():
                        flush_batch()
                        monitor_logger.info("monitor stopping, process exited")
                        return

                    try:
                        sample = monitor_loop.tick_once()
                        consecutive_failures = 0

                        payload_json = json.dumps(sample.fragments, separators=(",", ":"))
                        batch.append(
                            SampleRecord(
                                run_id=run_id,
                                ts_monotonic=sample.ts_monotonic,
                                ts_wall_epoch=sample.ts_wall_epoch,
                                payload_json=payload_json,
                            )
                        )

                        if len(batch) >= batch_size:
                            flush_batch()

                    except FileNotFoundError as exc:
                        # /proc can disappear during exit races.
                        if should_stop():
                            flush_batch()
                            monitor_logger.info("monitor stopping, /proc disappeared and process exited")
                            return

                        consecutive_failures += 1
                        monitor_logger.warning(
                            "monitor tick failed (%d/%d): %s",
                            consecutive_failures,
                            monitor_config.max_consecutive_failures,
                            exc,
                        )
                        self._store.append_event(
                            EventRecord(
                                run_id=run_id,
                                ts_monotonic=time.monotonic(),
                                event_type="WARN",
                                message=f"monitor tick failed: {exc}",
                                policy_id=None,
                            )
                        )

                    except Exception as exc:
                        consecutive_failures += 1
                        monitor_logger.warning(
                            "monitor tick failed (%d/%d): %s",
                            consecutive_failures,
                            monitor_config.max_consecutive_failures,
                            exc,
                        )
                        self._store.append_event(
                            EventRecord(
                                run_id=run_id,
                                ts_monotonic=time.monotonic(),
                                event_type="WARN",
                                message=f"monitor tick failed: {exc}",
                                policy_id=None,
                            )
                        )

                    if consecutive_failures >= monitor_config.max_consecutive_failures:
                        flush_batch()
                        monitor_logger.error("monitor aborting after repeated failures")
                        self._store.append_event(
                            EventRecord(
                                run_id=run_id,
                                ts_monotonic=time.monotonic(),
                                event_type="WARN",
                                message="monitor aborted after repeated failures",
                                policy_id=None,
                            )
                        )
                        return

                    next_tick += monitor_config.sample_interval_s

            monitor_thread = threading.Thread(
                target=monitor_worker,
                name=f"testguard-monitor-{run_id}",
                daemon=True,
            )
            monitor_thread.start()
        else:
            logger.info("monitor skipped, non-linux platform")

        stdout_bytes, stderr_bytes = handle.popen.communicate()
        (run_directory / "stdout.txt").write_bytes(stdout_bytes[:1024 * 1024])
        (run_directory / "stderr.txt").write_bytes(stderr_bytes[:1024 * 1024])

        if monitor_thread is not None:
            monitor_thread.join(timeout=2.0)

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
        logger.info(
            "completed status=%s exit_code=%s signal=%s duration_s=%.3f",
            status,
            exit_code,
            signal_number,
            duration_s,
        )
        return run_id
