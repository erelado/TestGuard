from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from testguard.monitor.collectors.linux.psi import PsiCollector
from testguard.monitor.collectors.linux.cgroupv2 import CgroupV2Collector
from testguard.controller import ProcessHandle, SubprocessGroupController
from testguard.logger import ContextLoggerAdapter, get_logger, get_named_logger
from testguard.monitor.collectors.base import Target
from testguard.monitor.collectors.linux.procfs import ProcfsCollector
from testguard.monitor.monitor_loop import MonitorConfig, MonitorLoop
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import SampleRecord
from testguard.util import ensure_dirs, is_linux, make_run_id, now_utc_iso, runs_dir
from .helpers import (
    KillState,
    OutputCapture,
    RunOptions,
    atomic_write_json,
    atomic_write_text,
    append_event,
    build_optional_governor,
    create_run_meta,
    make_ring_snapshot,
    start_output_capture,
    terminate_with_escalation,
    write_command_not_found_artifact,
)


class Engine:
    def __init__(self, store: SQLiteRunStore) -> None:
        self._store = store
        self._controller = SubprocessGroupController()

    def run_command(
            self,
            command_argv: List[str],
            cwd: str,
            env: Dict[str, str],
            *,
            sample_interval_s: float = 0.5,
            warn_memory_mib: int = 0,
            max_memory_mib: int = 0,
            max_runtime_s: Optional[float] = None,
            disk_write_mib_s: Optional[float] = None,
            disk_write_sustain_s: float = 3.0,
            signature_label: Optional[str] = None,
            tags: Optional[dict[str, str]] = None,
            kill_grace_s: float = 5.0,
            stdout_tail_kib: int = 256,
            stderr_tail_kib: int = 256,
    ) -> str:
        run_options = RunOptions(
            sample_interval_s=sample_interval_s,
            warn_rss_mib=warn_memory_mib,
            max_rss_mib=max_memory_mib,
            max_runtime_s=max_runtime_s,
            disk_write_mib_s=disk_write_mib_s,
            disk_write_sustain_s=disk_write_sustain_s,
            kill_grace_s=kill_grace_s,
            stdout_tail_kib=stdout_tail_kib,
            stderr_tail_kib=stderr_tail_kib,
        )
        return self._run_command_internal(command_argv=command_argv, cwd=cwd, env=env, run_options=run_options)

    def _run_command_internal(self, *, command_argv: List[str], cwd: str, env: Dict[str, str],
                              run_options: RunOptions) -> str:
        ensure_dirs()
        self._store.init()

        run_id = make_run_id()
        logger = get_logger(run_id=run_id)

        started_at = now_utc_iso()
        run_directory = runs_dir() / run_id
        run_directory.mkdir(parents=True, exist_ok=False)

        meta = create_run_meta(run_id=run_id, started_at=started_at, command_argv=command_argv, cwd=cwd,
                               run_options=run_options)
        self._store.create_run(meta)

        logger.info("spawn command: %s", command_argv)

        start_monotonic = time.monotonic()
        handle = self._spawn_or_record_failure(
            logger=logger,
            run_id=run_id,
            run_directory=run_directory,
            start_monotonic=start_monotonic,
            command_argv=command_argv,
            cwd=cwd,
            env=env,
        )
        if handle is None:
            return run_id

        output_capture = start_output_capture(handle=handle, run_directory=run_directory, run_id=run_id,
                                              run_options=run_options)

        kill_state = KillState()

        monitor_thread = self._maybe_start_linux_monitor(
            run_id=run_id,
            handle=handle,
            run_directory=run_directory,
            output_capture=output_capture,
            run_options=run_options,
            start_monotonic=start_monotonic,
            kill_state=kill_state,
            logger=logger,
        )

        handle.popen.wait()

        if monitor_thread is not None:
            monitor_thread.join(timeout=2.0)
        output_capture.join(timeout_s=2.0)

        self._finalize_run(
            run_id=run_id,
            logger=logger,
            start_monotonic=start_monotonic,
            kill_state=kill_state,
            return_code=handle.popen.returncode,
        )
        return run_id

    def _spawn_or_record_failure(
            self,
            *,
            logger: ContextLoggerAdapter,
            run_id: str,
            run_directory: Path,
            start_monotonic: float,
            command_argv: List[str],
            cwd: str,
            env: Dict[str, str],
    ) -> Optional[ProcessHandle]:
        try:
            return self._controller.spawn(argv=command_argv, cwd=cwd, env=env)
        except FileNotFoundError:
            missing_executable = command_argv[0] if command_argv else "<empty>"
            logger.error("command not found: %s", missing_executable)

            notes = json.dumps({"error": "command_not_found", "executable": missing_executable}, separators=(",", ":"))
            self._store.finalize_run(
                run_id,
                ended_at=now_utc_iso(),
                status="FAILED",
                exit_code=127,
                signal=None,
                duration_s=time.monotonic() - start_monotonic,
                notes=notes,
            )
            write_command_not_found_artifact(run_directory, missing_executable)
            return None

    def _maybe_start_linux_monitor(
            self,
            *,
            run_id: str,
            handle: ProcessHandle,
            run_directory: Path,
            output_capture: OutputCapture,
            run_options: RunOptions,
            start_monotonic: float,
            kill_state: KillState,
            logger: ContextLoggerAdapter,
    ) -> Optional[threading.Thread]:
        if not is_linux():
            logger.info("monitoring and governor disabled on non-linux platform")
            return None

        monitor_logger = get_named_logger("testguard.monitor", run_id=run_id)
        governor = build_optional_governor(run_options)

        collectors = []
        cgroup_collector = CgroupV2Collector.for_run(run_id=run_id, pid=handle.popen.pid)
        if cgroup_collector is not None:
            collectors.append(cgroup_collector)

        collectors.append(ProcfsCollector())
        collectors.append(PsiCollector())

        monitor_config = MonitorConfig(
            sample_interval_s=run_options.sample_interval_s,
            max_consecutive_failures=3,
            ring_buffer_seconds=30.0,
        )
        monitor_loop = MonitorLoop(
            collectors=collectors,
            target=Target(root_pid=handle.popen.pid),
            config=monitor_config,
            logger=monitor_logger,
        )

        def should_stop() -> bool:
            return handle.popen.poll() is not None

        def panic_kill(*, policy_id: str, message: str, final_fragments: dict) -> None:
            first = kill_state.mark_first_kill(policy_id=policy_id, message=message)
            if not first:
                return

            append_event(self._store, run_id=run_id, event_type="PANIC", message=message, policy_id=policy_id)

            ring_snapshot = make_ring_snapshot(monitor_loop)
            post_mortem = {
                "run_id": run_id,
                "policy_id": policy_id,
                "message": message,
                "final_fragments": final_fragments,
                "ring_buffer": ring_snapshot,
                "stdout_tail": output_capture.stdout_tail.get_text(),
                "stderr_tail": output_capture.stderr_tail.get_text(),
            }
            atomic_write_json(run_directory / "post_mortem.json", post_mortem)

            md_lines = [
                "# TestGuard post-mortem",
                "",
                f"run_id: {run_id}",
                f"policy_id: {policy_id}",
                f"reason: {message}",
                "",
                "## stdout tail",
                "```",
                output_capture.stdout_tail.get_text(),
                "```",
                "",
                "## stderr tail",
                "```",
                output_capture.stderr_tail.get_text(),
                "```",
            ]
            atomic_write_text(run_directory / "post_mortem.md", "\n".join(md_lines))

            terminate_with_escalation(
                store=self._store,
                controller=self._controller,
                handle=handle,
                run_id=run_id,
                policy_id=policy_id,
                kill_grace_s=run_options.kill_grace_s,
            )

        def monitor_worker() -> None:
            batch: List[SampleRecord] = []
            batch_size = 50
            consecutive_failures = 0
            next_tick = time.monotonic()

            monitor_logger.info("monitor started interval_s=%.3f", monitor_config.sample_interval_s)

            def flush_batch() -> None:
                if not batch:
                    return
                self._store.append_samples(run_id=run_id, samples=list(batch))
                batch.clear()

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

                    batch.append(
                        SampleRecord(
                            run_id=run_id,
                            ts_monotonic=sample.ts_monotonic,
                            ts_wall_epoch=sample.ts_wall_epoch,
                            payload_json=json.dumps(sample.fragments, separators=(",", ":")),
                        )
                    )
                    if len(batch) >= batch_size:
                        flush_batch()

                    if governor is not None:
                        decision = governor.evaluate(
                            sample_ts=sample.ts_monotonic,
                            fragments=sample.fragments,
                            started_monotonic=start_monotonic,
                        )

                        if decision.level == "WARN":
                            append_event(
                                self._store,
                                run_id=run_id,
                                event_type="WARN",
                                message=decision.message or "warning",
                                policy_id=decision.policy_id,
                            )
                            monitor_logger.warning("%s", decision.message)

                        if decision.level == "PANIC":
                            panic_kill(
                                policy_id=decision.policy_id or "unknown",
                                message=decision.message or "panic",
                                final_fragments=sample.fragments,
                            )
                            flush_batch()
                            return

                except Exception as exc:
                    consecutive_failures += 1
                    monitor_logger.warning(
                        "monitor tick failed (%d/%d): %s",
                        consecutive_failures,
                        monitor_config.max_consecutive_failures,
                        exc,
                    )
                    append_event(
                        self._store,
                        run_id=run_id,
                        event_type="WARN",
                        message=f"monitor tick failed: {exc}",
                        policy_id=None,
                    )

                    if consecutive_failures >= monitor_config.max_consecutive_failures:
                        flush_batch()
                        monitor_logger.error("monitor aborting after repeated failures")
                        return

                next_tick += monitor_config.sample_interval_s

        monitor_thread = threading.Thread(
            target=monitor_worker,
            name=f"testguard-monitor-{run_id}",
            daemon=True,
        )
        monitor_thread.start()
        return monitor_thread

    def _finalize_run(
            self,
            *,
            run_id: str,
            logger: ContextLoggerAdapter,
            start_monotonic: float,
            kill_state: KillState,
            return_code: Optional[int],
    ) -> None:
        duration_s = time.monotonic() - start_monotonic
        ended_at = now_utc_iso()

        exit_code: Optional[int] = None
        signal_number: Optional[int] = None

        if return_code is None:
            status = "UNKNOWN"
        elif return_code < 0:
            status = "FAILED"
            signal_number = -return_code
        else:
            exit_code = return_code
            status = "OK" if return_code == 0 else "FAILED"

        notes = kill_state.notes_json_or_none()
        if kill_state.was_killed():
            status = "KILLED"

        self._store.finalize_run(
            run_id,
            ended_at=ended_at,
            status=status,
            exit_code=exit_code,
            signal=signal_number,
            duration_s=duration_s,
            notes=notes,
        )

        logger.info(
            "completed status=%s exit_code=%s signal=%s duration_s=%.3f",
            status,
            exit_code,
            signal_number,
            duration_s,
        )
