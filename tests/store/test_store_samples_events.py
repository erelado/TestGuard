import json
import tempfile
import unittest
from pathlib import Path

from testguard.store.sqlite_store import SQLiteRunStore
from testguard.store.store import EventRecord, SampleRecord


class TestSQLiteSamplesEvents(unittest.TestCase):
    def test_append_samples_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "runs.db"
            store = SQLiteRunStore(database_path=database_path)
            store.init()

            samples = [
                SampleRecord(run_id="r1", ts_monotonic=1.0, ts_wall_epoch=100.0, payload_json=json.dumps({"a": 1})),
                SampleRecord(run_id="r1", ts_monotonic=2.0, ts_wall_epoch=101.0, payload_json=json.dumps({"a": 2})),
            ]
            store.append_samples(run_id="r1", samples=samples)

            event = EventRecord(run_id="r1", ts_monotonic=2.5, event_type="WARN", message="something", policy_id=None)
            store.append_event(event=event)

            # Verify row counts with a direct sqlite read.
            connection = store._connect()
            with connection:
                sample_count = connection.execute("SELECT COUNT(*) AS c FROM samples WHERE run_id = ?", ("r1",)).fetchone()["c"]
                event_count = connection.execute("SELECT COUNT(*) AS c FROM events WHERE run_id = ?", ("r1",)).fetchone()["c"]

            self.assertEqual(sample_count, 2)
            self.assertEqual(event_count, 1)
