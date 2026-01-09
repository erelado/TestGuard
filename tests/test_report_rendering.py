import json
import tempfile
import unittest
from pathlib import Path

from testguard.analyze import RunMetrics, no_baseline_diff
from testguard.report import render_list, render_report
from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import RunMeta
from testguard.util import signature_hash


class TestReportRendering(unittest.TestCase):
    def test_render_list_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "runs.db"
            store = SQLiteRunStore(database_path)
            store.init()

            command_argv = ["python", "-c", "print('x')"]
            cwd = "/tmp"
            meta = RunMeta(
                run_id="20260109_000000_abcd",
                started_at="2026-01-09T00:00:00Z",
                command_argv=command_argv,
                cwd=cwd,
                signature_hash=signature_hash(command_argv, cwd),
                host_facts_json=json.dumps({"os": "darwin"}),
                run_config_json=json.dumps({}, separators=(",", ":")),
            )
            store.create_run(meta)
            store.finalize_run(
                meta.run_id,
                ended_at="2026-01-09T00:00:02Z",
                status="OK",
                exit_code=0,
                signal=None,
                duration_s=2.0,
                notes=None,
            )

            run_records = store.list_runs(limit=10)
            list_text = render_list(run_records)
            self.assertIn("20260109_000000_abcd", list_text)

            run_record = store.load_run(meta.run_id)

            # On macOS in this MVP, samples may be empty, so metrics are mostly None.
            current_metrics = RunMetrics(
                duration_s=run_record.duration_s,
                peak_rss_bytes=None,
                total_read_bytes=None,
                total_write_bytes=None,
                peak_write_rate_bytes_s=None,
            )
            diff_summary = no_baseline_diff(current_metrics)

            report_text = render_report(run_record, current_metrics=current_metrics, diff_summary=diff_summary)
            self.assertIn("run_id: 20260109_000000_abcd", report_text)
            self.assertIn("status:", report_text)
            self.assertIn("metrics:", report_text)
            self.assertIn("diff vs baseline:", report_text)
            self.assertIn("recommendations:", report_text)
