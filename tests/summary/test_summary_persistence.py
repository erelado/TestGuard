import json
import tempfile
import unittest
from pathlib import Path

from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import RunMeta, SampleRecord
from testguard.summarize import persist_summary_for_run
from testguard.util import signature_hash


class TestSummaryPersistence(unittest.TestCase):
    def test_persist_summary_writes_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "runs.db"
            store = SQLiteRunStore(database_path)
            store.init()

            command_argv = ["python", "-c", "print('x')"]
            cwd = "/tmp"
            meta = RunMeta(
                run_id="r1",
                started_at="2026-01-09T00:00:00Z",
                command_argv=command_argv,
                cwd=cwd,
                signature_hash=signature_hash(command_argv, cwd),
                host_facts_json=json.dumps({"os": "linux"}),
                run_config_json=json.dumps({}, separators=(",", ":")),
            )
            store.create_run(meta)
            store.finalize_run("r1", ended_at="2026-01-09T00:00:01Z", status="OK", exit_code=0, signal=None, duration_s=1.0, notes=None)

            # Fake samples
            store.append_samples(
                "r1",
                [
                    SampleRecord(run_id="r1", ts_monotonic=1.0, ts_wall_epoch=0.0, payload_json=json.dumps({"rss_bytes": 100, "io_write_bytes_total": 0})),
                    SampleRecord(run_id="r1", ts_monotonic=2.0, ts_wall_epoch=0.0, payload_json=json.dumps({"rss_bytes": 200, "io_write_bytes_total": 1000})),
                ],
            )

            run_record = store.load_run("r1")
            summary = persist_summary_for_run(store, run_record)

            self.assertEqual(summary.peak_rss_bytes, 200)
            self.assertEqual(summary.total_write_bytes, 1000)

            reloaded = store.load_run("r1")
            self.assertEqual(reloaded.peak_rss_bytes, 200)
            self.assertEqual(reloaded.total_write_bytes, 1000)
