from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from testguard.controller import SubprocessGroupController
from testguard.governor import Governor, Thresholds
from testguard.logger import get_logger, get_named_logger
from testguard.monitor.collectors.base import Target
from testguard.monitor.collectors.procfs import ProcfsCollector
from testguard.monitor.monitor_loop import MonitorConfig, MonitorLoop
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import EventRecord, RunMeta, SampleRecord
from testguard.tail import TailBuffer
from testguard.util import (
    ensure_dirs,
    host_facts,
    is_linux,
    make_run_id,
    now_utc_iso,
    runs_dir,
    signature_hash,
)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _atomic_write_json(path: Path, obj: object) -> None:
    _atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True))


def _stream_pipe_to_file_and_tail(pipe, output_path: Path, tail: TailBuffer) -> None:
    with output_path.open("wb") as f:
        while True:
            chunk = pipe.read(8192)
            if not chunk:
                break
            f.write(chunk)
            tail.append(chunk)


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
            warn_rss_mib: int = 0,
            max_rss_mib: int = 0,
            max_runtime_s: Optional[float] = None,
            disk_write_mib_s: Optional[float] = None,
            disk_write_sustain_s: float = 3.0,
            kill_grace_s: float = 5.0,
            stdout_tail_kib: int = 256,
            stderr_tail_kib: int = 256,
    ) -> str:
        ensure_dirs()
        self._store.init()

        run_id = make_run_id()
        logger = get_logger(run_id=run_id)

        started_at = now_utc_iso()
        run_directory = runs_dir() / run_id
        run_directory.mkdir(parents=True, exist_ok=False)

        run_config = {
            "sample_interval_s": sample_interval_s,
            "warn_rss_mib": warn_rss_mib,
            "max_rss_mib": max_rss_mib,
            "max_runtime_s": max_runtime_s,
            "disk_write_mib_s": disk_write_mib_s,
            "disk_write_sustain_s": disk_write_sustain_s,
            "kill_grace_s": kill_grace_s,
            "stdout_tail_kib": stdout_tail_kib,
            "stderr_tail_kib": stderr_tail_kib,
        }

        facts = host_facts()
        meta = RunMeta(
            run_id=run_id,
            started_at=started_at,
            command_argv=command_argv,
            cwd=cwd,
            signature_hash=signature_hash(command_argv, cwd),
            host_facts_json=json.dumps(facts.__dict__, separators=(",", ":")),
            run_config_json=json.dumps(run_config, separators=(",", ":")),
        )
        self._store.create_run(meta)

        logger.info("spawn command: %s", command_argv)

        start_monotonic = time.monotonic()
        try:
            handle = self._controller.spawn(argv=command_argv, cwd=cwd, env=env)
        except FileNotFoundError as exc:
            missing_executable = command_argv[0] if command_argv else "<empty>"
            logger.error("command not found: %s", missing_executable)

            hint_lines = [
                f"Command not found: {missing_executable}",
                "Tips:",
                "  - If you use uv, run the command via: uv run testguard run -- <cmd>",
                "  - For pytest specifically, use: python -m pytest",
            ]
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

            (run_directory / "error.txt").write_text("\n".join(hint_lines), encoding="utf-8")
            return run_id

        stdout_path = run_directory / "stdout.log"
        stderr_path = run_directory / "stderr.log"
        stdout_tail = TailBuffer(max_bytes=stdout_tail_kib * 1024)
        stderr_tail = TailBuffer(max_bytes=stderr_tail_kib * 1024)

        stdout_thread = threading.Thread(
            target=_stream_pipe_to_file_and_tail,
            args=(handle.popen.stdout, stdout_path, stdout_tail),
            name=f"testguard-stdout-{run_id}",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_stream_pipe_to_file_and_tail,
            args=(handle.popen.stderr, stderr_path, stderr_tail),
            name=f"testguard-stderr-{run_id}",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        def should_stop() -> bool:
            return handle.popen.poll() is not None

        kill_lock = threading.Lock()
        killed_by_policy: Optional[dict] = None

        def request_kill(*, policy_id: str, message: str, ring_snapshot: list, final_fragments: dict) -> None:
            nonlocal killed_by_policy
            with kill_lock:
                if killed_by_policy is not None:
                    return
                killed_by_policy = {
                    "policy_id": policy_id,
                    "message": message,
                    "triggered_at_monotonic": time.monotonic(),
                }

            self._store.append_event(
                EventRecord(
                    run_id=run_id,
                    ts_monotonic=time.monotonic(),
                    event_type="PANIC",
                    message=message,
                    policy_id=policy_id,
                )
            )

            post_mortem = {
                "run_id": run_id,
                "policy_id": policy_id,
                "message": message,
                "final_fragments": final_fragments,
                "ring_buffer": ring_snapshot,
                "stdout_tail": stdout_tail.get_text(),
                "stderr_tail": stderr_tail.get_text(),
            }
            _atomic_write_json(run_directory / "post_mortem.json", post_mortem)

            md_lines = [
                f"# TestGuard post-mortem",
                "",
                f"run_id: {run_id}",
                f"policy_id: {policy_id}",
                f"reason: {message}",
                "",
                "## stdout tail",
                "```",
                stdout_tail.get_text(),
                "```",
                "",
                "## stderr tail",
                "```",
                stderr_tail.get_text(),
                "```",
            ]
            _atomic_write_text(run_directory / "post_mortem.md", "\n".join(md_lines))

            self._store.append_event(
                EventRecord(
                    run_id=run_id,
                    ts_monotonic=time.monotonic(),
                    event_type="TERM_SENT",
                    message="sent SIGTERM to process group",
                    policy_id=policy_id,
                )
            )
            self._controller.terminate_process_group(handle)

            exited = self._controller.wait_for_exit(handle, timeout_s=kill_grace_s)
            if exited is None:
                self._store.append_event(
                    EventRecord(
                        run_id=run_id,
                        ts_monotonic=time.monotonic(),
                        event_type="KILL_SENT",
                        message="sent SIGKILL to process group",
                        policy_id=policy_id,
                    )
                )
                self._controller.kill_process_group(handle)

        monitor_thread: Optional[threading.Thread] = None

        if is_linux():
            monitor_logger = get_named_logger("testguard.monitor", run_id=run_id)

            rss_warn_bytes = warn_rss_mib * 1024 * 1024
            rss_max_bytes = max_rss_mib * 1024 * 1024

            # If user did not set thresholds, keep governor inactive (Linux-only enforcement begins when configured).
            governor: Optional[Governor] = None
            if rss_warn_bytes > 0 and rss_max_bytes > 0:
                thresholds = Thresholds(
                    warn_rss_bytes=rss_warn_bytes,
                    max_rss_bytes=rss_max_bytes,
                    max_runtime_s=max_runtime_s,
                    disk_write_rate_bytes_s=(disk_write_mib_s * 1024 * 1024) if disk_write_mib_s is not None else None,
                    disk_write_sustain_s=disk_write_sustain_s,
                )
                governor = Governor(thresholds)

            monitor_config = MonitorConfig(
                sample_interval_s=sample_interval_s,
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

                monitor_logger.info("monitor started interval_s=%.3f", monitor_config.sample_interval_s)
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

                        if governor is not None:
                            decision = governor.evaluate(
                                sample_ts=sample.ts_monotonic,
                                fragments=sample.fragments,
                                started_monotonic=start_monotonic,
                            )
                            if decision.level == "WARN":
                                self._store.append_event(
                                    EventRecord(
                                        run_id=run_id,
                                        ts_monotonic=time.monotonic(),
                                        event_type="WARN",
                                        message=decision.message or "warning",
                                        policy_id=decision.policy_id,
                                    )
                                )
                                monitor_logger.warning("%s", decision.message)

                            if decision.level == "PANIC":
                                ring_snapshot = [
                                    {"ts_monotonic": s.ts_monotonic, "ts_wall_epoch": s.ts_wall_epoch,
                                     "fragments": s.fragments}
                                    for s in monitor_loop.ring_buffer.snapshot()
                                ]
                                request_kill(
                                    policy_id=decision.policy_id or "unknown",
                                    message=decision.message or "panic",
                                    ring_snapshot=ring_snapshot,
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
                            return

                    next_tick += monitor_config.sample_interval_s

            monitor_thread = threading.Thread(
                target=monitor_worker,
                name=f"testguard-monitor-{run_id}",
                daemon=True,
            )
            monitor_thread.start()
        else:
            logger.info("monitoring and governor disabled on non-linux platform")

        handle.popen.wait()

        if monitor_thread is not None:
            monitor_thread.join(timeout=2.0)

        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

        duration_s = time.monotonic() - start_monotonic
        ended_at = now_utc_iso()

        return_code = handle.popen.returncode
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

        notes = None
        with kill_lock:
            if killed_by_policy is not None:
                status = "KILLED"
                notes = json.dumps(killed_by_policy, separators=(",", ":"))

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
        return run_id
