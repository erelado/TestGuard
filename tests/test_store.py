import json
import tempfile
import unittest
from pathlib import Path

from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import RunMeta
from testguard.util import signature_hash


class TestSQLiteRunStore(unittest.TestCase):
    def test_create_finalize_load(self) -> None:
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
                host_facts_json=json.dumps({"k": "v"}),
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

            run_record = store.load_run(meta.run_id)
            self.assertEqual(run_record.run_id, meta.run_id)
            self.assertEqual(run_record.status, "OK")
            self.assertEqual(run_record.exit_code, 0)
            self.assertIsNone(run_record.signal)
