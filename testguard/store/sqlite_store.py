from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from testguard.store.retention import RetentionRun
from testguard.store.store import (
    EventRecord,
    RunMeta,
    RunRecord,
    SampleRecord,
    RunSummaryRecord,
)

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  command_argv_json TEXT NOT NULL,
  cwd TEXT NOT NULL,
  signature_hash TEXT NOT NULL,
  host_facts_json TEXT NOT NULL,
  run_config_json TEXT NOT NULL,
  status TEXT NOT NULL,
  exit_code INTEGER,
  signal INTEGER,
  duration_s REAL,
  notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_signature_started
ON runs(signature_hash, started_at);

CREATE TABLE IF NOT EXISTS samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts_monotonic REAL NOT NULL,
  ts_wall_epoch REAL NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_samples_run_id
ON samples(run_id);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  ts_monotonic REAL NOT NULL,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL,
  policy_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_run_id
ON events(run_id);

CREATE TABLE IF NOT EXISTS summaries (
  run_id TEXT PRIMARY KEY,
  peak_rss_bytes INTEGER,
  total_read_bytes INTEGER,
  total_write_bytes INTEGER,
  peak_write_rate_bytes_s REAL,
  warnings_count INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
"""


class SQLiteRunStore:
    """SQLite-backed local persistence for TestGuard runs."""

    def __init__(self, *, database_path: Path) -> None:
        self._database_path = database_path
        self.init()

    # Internal helpers

    def _connect(self) -> sqlite3.Connection:
        # Ensure parent exists even on a brand new CI runner.
        self._database_path.parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row

        # Good hygiene. Foreign keys are off by default per-connection in SQLite.
        connection.execute("PRAGMA foreign_keys=ON;")
        return connection

    def _ensure_column(
            self, connection: sqlite3.Connection, table: str, column: str, column_def: str
    ) -> None:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row["name"] for row in rows}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")

    def init(self) -> None:
        """Ensure DB and schema exist."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    # Run lifecycle

    def create_run(self, meta: RunMeta) -> None:
        self.init()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, started_at, command_argv_json, cwd,
                    signature_hash, host_facts_json, run_config_json, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta.run_id,
                    meta.started_at,
                    json.dumps(meta.command_argv, separators=(",", ":")),
                    meta.cwd,
                    meta.signature_hash,
                    meta.host_facts_json,
                    meta.run_config_json,
                    "RUNNING",
                ),
            )

    def finalize_run(
            self,
            run_id: str,
            *,
            ended_at: str,
            status: str,
            exit_code: Optional[int],
            signal: Optional[int],
            duration_s: float,
            notes: Optional[str] = None,
    ) -> None:
        """Mark a run as finished, regardless of success or failure."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET ended_at   = ?,
                    status     = ?,
                    exit_code  = ?,
                    signal     = ?,
                    duration_s = ?,
                    notes      = ?
                WHERE run_id = ?
                """,
                (ended_at, status, exit_code, signal, duration_s, notes, run_id),
            )

    # Queries

    def load_run(self, *, run_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*,
                       s.peak_rss_bytes,
                       s.total_read_bytes,
                       s.total_write_bytes,
                       s.peak_write_rate_bytes_s,
                       s.warnings_count
                FROM runs r
                         LEFT JOIN summaries s ON s.run_id = r.run_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        return RunRecord(**dict(row))

    def list_runs(self, *, limit: int = 50) -> List[RunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.*,
                       s.peak_rss_bytes,
                       s.total_read_bytes,
                       s.total_write_bytes,
                       s.peak_write_rate_bytes_s,
                       s.warnings_count
                FROM runs r
                         LEFT JOIN summaries s ON s.run_id = r.run_id
                ORDER BY r.started_at DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [RunRecord(**dict(row)) for row in rows]

    # Samples / events / summaries

    def append_samples(self, *, run_id: str, samples: List[SampleRecord]) -> None:
        if not samples:
            return
        for s in samples:
            assert s.run_id == run_id, f"sample.run_id mismatch: {s.run_id} != {run_id}"
        rows = [(run_id, s.ts_monotonic, s.ts_wall_epoch, s.payload_json) for s in samples]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO samples (run_id, ts_monotonic, ts_wall_epoch, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )

    def append_event(self, *, event: EventRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events (run_id, ts_monotonic, event_type, message, policy_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    event.ts_monotonic,
                    event.event_type,
                    event.message,
                    event.policy_id,
                ),
            )

    def upsert_summary(self, *, summary: RunSummaryRecord) -> None:
        self.init()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO summaries (run_id,
                                       peak_rss_bytes,
                                       total_read_bytes,
                                       total_write_bytes,
                                       peak_write_rate_bytes_s,
                                       warnings_count)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id) DO
                UPDATE SET
                    peak_rss_bytes = excluded.peak_rss_bytes,
                    total_read_bytes = excluded.total_read_bytes,
                    total_write_bytes = excluded.total_write_bytes,
                    peak_write_rate_bytes_s = excluded.peak_write_rate_bytes_s,
                    warnings_count = excluded.warnings_count
                """,
                (
                    summary.run_id,
                    summary.peak_rss_bytes,
                    summary.total_read_bytes,
                    summary.total_write_bytes,
                    summary.peak_write_rate_bytes_s,
                    int(summary.warnings_count),
                ),
            )

    def count_warnings(self, *, run_id: str) -> int:
        self.init()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(1) AS n
                FROM events
                WHERE run_id = ?
                  AND event_type = 'WARN'
                """,
                (run_id,),
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    # Baseline / retention

    def find_baseline_run_id(
            self, signature_hash: str, *, exclude_run_id: Optional[str] = None
    ) -> Optional[str]:
        """Return the most recent successful baseline run with the same signature."""
        self.init()
        query = """
                SELECT run_id
                FROM runs
                WHERE signature_hash = ?
                  AND status = 'OK'
                  AND exit_code = 0 \
                """
        params: list = [signature_hash]
        if exclude_run_id:
            query += " AND run_id != ?"
            params.append(exclude_run_id)
        query += " ORDER BY started_at DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return str(row["run_id"]) if row else None

    def list_samples(self, *, run_id: str) -> List[SampleRecord]:
        self.init()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, ts_monotonic, ts_wall_epoch, payload_json
                FROM samples
                WHERE run_id = ?
                ORDER BY ts_monotonic ASC
                """,
                (run_id,),
            ).fetchall()
        return [SampleRecord(**dict(row)) for row in rows]

    def list_runs_for_retention(self) -> Iterable[RetentionRun]:
        """
        Return all runs with enough metadata to prune retention.
        Uses ended_at as finished_at_epoch (converted to timestamp if ISO string).
        """
        self.init()
        with self._connect() as connection:
            cursor = connection.execute(
                "SELECT run_id, ended_at, cwd FROM runs WHERE ended_at IS NOT NULL"
            )
            for run_id, ended_at, cwd in cursor.fetchall():
                try:
                    finished_at_epoch = datetime.fromisoformat(ended_at).timestamp()
                except Exception:
                    finished_at_epoch = 0.0
                yield RetentionRun(
                    run_id=run_id,
                    finished_at_epoch=finished_at_epoch,
                    artifacts_dir=(Path(cwd) / run_id) if cwd else None,
                )

    def delete_run(self, *, run_id: str) -> None:
        """Completely remove a run and its child rows."""
        self.init()
        with self._connect() as connection:
            connection.execute("DELETE FROM samples WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM summaries WHERE run_id = ?", (run_id,))
            connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    # Cleanup helpers

    def mark_stale_runs_as_error(self) -> None:
        """
        Convert any 'RUNNING' runs with no matching process or missing report
        into status='ERROR', ended_at=now.
        Called at startup to clean up stale metadata.
        """
        now = datetime.utcnow().isoformat() + "Z"
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status   = 'ERROR',
                    ended_at = ?,
                    notes    = 'auto-cleanup: stale RUNNING entry'
                WHERE status = 'RUNNING'
                  AND ended_at IS NULL
                """,
                (now,),
            )
