import json
import unittest

from testguard.analysis import compute_metrics
from testguard.store.store import RunRecord, SampleRecord


class TestComputeMetrics(unittest.TestCase):
    def test_compute_metrics_peak_rss_and_io(self) -> None:
        run_record = RunRecord(
            run_id="r1",
            started_at="2026-01-09T00:00:00Z",
            ended_at="2026-01-09T00:00:01Z",
            command_argv_json='["python","-c","print(1)"]',
            cwd="/tmp",
            signature_hash="sig",
            host_facts_json="{}",
            run_config_json="{}",
            status="OK",
            exit_code=0,
            signal=None,
            duration_s=1.0,
            notes=None,
        )

        samples = [
            SampleRecord(
                run_id="r1",
                ts_monotonic=1.0,
                ts_wall_epoch=0.0,
                payload_json=json.dumps({"rss_bytes": 100, "io_read_bytes_total": 10, "io_write_bytes_total": 100}),
            ),
            SampleRecord(
                run_id="r1",
                ts_monotonic=2.0,
                ts_wall_epoch=0.0,
                payload_json=json.dumps({"rss_bytes": 250, "io_read_bytes_total": 30, "io_write_bytes_total": 500}),
            ),
            SampleRecord(
                run_id="r1",
                ts_monotonic=3.0,
                ts_wall_epoch=0.0,
                payload_json=json.dumps({"rss_bytes": 200, "io_read_bytes_total": 40, "io_write_bytes_total": 900}),
            ),
        ]

        metrics = compute_metrics(run_record, samples)

        self.assertEqual(metrics.peak_memory_bytes, 250)
        self.assertEqual(metrics.total_read_bytes, 40 - 10)
        self.assertEqual(metrics.total_write_bytes, 900 - 100)
        # write deltas: 400 in 1s, then 400 in 1s => peak 400 B/s
        self.assertEqual(metrics.peak_write_rate_bytes_per_second, 400.0)
