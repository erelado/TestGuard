from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol


@dataclass(frozen=True, kw_only=True)
class RunMeta:
    run_id: str
    started_at: str
    command_argv: List[str]
    cwd: str
    signature_hash: str
    host_facts_json: str
    run_config_json: str


@dataclass(frozen=True, kw_only=True)
class RunRecord:
    run_id: str
    started_at: str
    ended_at: Optional[str]
    command_argv_json: str
    cwd: str
    signature_hash: str
    host_facts_json: str
    run_config_json: str
    status: str
    exit_code: Optional[int]
    signal: Optional[int]
    duration_s: Optional[float]
    notes: Optional[str]
    peak_rss_bytes: Optional[int] = None
    total_read_bytes: Optional[int] = None
    total_write_bytes: Optional[int] = None
    peak_write_rate_bytes_s: Optional[float] = None
    warnings_count: int = 0


@dataclass(frozen=True, kw_only=True)
class SampleRecord:
    run_id: str
    ts_monotonic: float
    ts_wall_epoch: float
    payload_json: str


@dataclass(frozen=True, kw_only=True)
class EventRecord:
    run_id: str
    ts_monotonic: float
    event_type: str
    message: str
    policy_id: Optional[str]

@dataclass(frozen=True, kw_only=True)
class RunSummaryRecord:
    run_id: str
    peak_rss_bytes: Optional[int]
    total_read_bytes: Optional[int]
    total_write_bytes: Optional[int]
    peak_write_rate_bytes_s: Optional[float]
    warnings_count: int



class RunStore(Protocol):
    def create_run(self, meta: RunMeta) -> None: ...

    def append_samples(self, run_id: str, samples: List[SampleRecord]) -> None: ...

    def append_event(self, event: EventRecord) -> None: ...

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
