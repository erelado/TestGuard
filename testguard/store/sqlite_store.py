from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from testguard.store.store import RunMeta, RunRecord

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
  status TEXT NOT NULL,
  exit_code INTEGER,
  signal INTEGER,
  duration_s REAL,
  notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_signature_started
ON runs(signature_hash, started_at);
"""


class SQLiteRunStore:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def create_run(self, meta: RunMeta) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (run_id, started_at, command_argv_json, cwd,
                                  signature_hash, host_facts_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta.run_id,
                    meta.started_at,
                    json.dumps(meta.command_argv, separators=(",", ":")),
                    meta.cwd,
                    meta.signature_hash,
                    meta.host_facts_json,
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
