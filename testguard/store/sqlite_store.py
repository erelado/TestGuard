from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from testguard.store.store import EventRecord, RunMeta, RunRecord, SampleRecord

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
"""


class SQLiteRunStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, column_def: str) -> None:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row["name"] for row in rows}
        if column in existing:
            return
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")

    def init(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            self._ensure_column(connection, "runs", "run_config_json", "TEXT NOT NULL DEFAULT '{}'")

    def create_run(self, meta: RunMeta) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (run_id, started_at, command_argv_json, cwd,
                                  signature_hash, host_facts_json, run_config_json, status)
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

    def load_run(self, run_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown run_id: {run_id}")
            return RunRecord(**dict(row))

    def list_runs(self, limit: int = 50) -> List[RunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [RunRecord(**dict(row)) for row in rows]

    def append_samples(self, run_id: str, samples: List[SampleRecord]) -> None:
        if not samples:
            return
        rows = [(s.run_id, s.ts_monotonic, s.ts_wall_epoch, s.payload_json) for s in samples]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO samples (run_id, ts_monotonic, ts_wall_epoch, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )

    def append_event(self, event: EventRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events (run_id, ts_monotonic, event_type, message, policy_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event.run_id, event.ts_monotonic, event.event_type, event.message, event.policy_id),
            )

    def find_baseline_run_id(self, signature_hash: str, *, exclude_run_id: Optional[str] = None) -> Optional[str]:
        self.init()
        with self._connect() as connection:
            if exclude_run_id is None:
                row = connection.execute(
                    """
                    SELECT run_id
                    FROM runs
                    WHERE signature_hash = ?
                      AND status = 'OK'
                      AND exit_code = 0
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (signature_hash,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT run_id
                    FROM runs
                    WHERE signature_hash = ?
                      AND status = 'OK'
                      AND exit_code = 0
                      AND run_id != ?
                    ORDER BY started_at DESC
                        LIMIT 1
                    """,
                    (signature_hash, exclude_run_id),
                ).fetchone()

            return None if row is None else str(row["run_id"])

    def list_samples(self, run_id: str) -> List[SampleRecord]:
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
