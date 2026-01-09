import json
import tempfile
import unittest
from pathlib import Path

from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import RunMeta


class TestBaselineSelection(unittest.TestCase):
    def test_find_baseline_excludes_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "runs.db"
            store = SQLiteRunStore(db_file)
            store.init()

            signature = "abc"

            meta1 = RunMeta(
                run_id="r1",
                started_at="2026-01-01T00:00:00Z",
                command_argv=["python", "-c", "print(1)"],
                cwd=".",
                signature_hash=signature,
                host_facts_json=json.dumps({}),
                run_config_json=json.dumps({}),
            )
            store.create_run(meta1)
            store.finalize_run("r1", ended_at="2026-01-01T00:00:01Z", status="OK", exit_code=0, signal=None, duration_s=1.0, notes=None)

            meta2 = RunMeta(
                run_id="r2",
                started_at="2026-01-02T00:00:00Z",
                command_argv=["python", "-c", "print(1)"],
                cwd=".",
                signature_hash=signature,
                host_facts_json=json.dumps({}),
                run_config_json=json.dumps({}),
            )
            store.create_run(meta2)
            store.finalize_run("r2", ended_at="2026-01-02T00:00:01Z", status="OK", exit_code=0, signal=None, duration_s=1.0, notes=None)

            baseline = store.find_baseline_run_id(signature, exclude_run_id="r2")
            self.assertEqual(baseline, "r1")
