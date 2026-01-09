from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol


@dataclass(frozen=True)
class RunMeta:
    run_id: str
    started_at: str
    command_argv: List[str]
    cwd: str
    signature_hash: str
    host_facts_json: str


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    started_at: str
    ended_at: Optional[str]
    command_argv_json: str
    cwd: str
    signature_hash: str
    host_facts_json: str
    status: str
    exit_code: Optional[int]
    signal: Optional[int]
    duration_s: Optional[float]
    notes: Optional[str]


class RunStore(Protocol):
    def create_run(self, meta: RunMeta) -> None: ...

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
    ) -> None: ...

    def load_run(self, run_id: str) -> RunRecord: ...

    def list_runs(self, limit: int = 50) -> List[RunRecord]: ...
