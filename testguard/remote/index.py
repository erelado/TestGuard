from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True, kw_only=True)
class RunIndexRecord:
    """
    Small, query-friendly record for baseline selection across machines

    Stored under:
      index/by_signature/<signature_hash>/<sortable_started_at>_<run_id>.json
    """

    run_id: str
    started_at: str
    ended_at: Optional[str]
    status: str
    exit_code: Optional[int]
    signal: Optional[int]
    signature_hash: str

    signature_label: Optional[str]
    tags: Dict[str, str]

    metrics: Dict[str, Any]
    artifacts_prefix: str  # runs/<run_id>/


def _sortable_started_at(started_at_iso: str) -> str:
    """
    Convert ISO like 2026-01-10T12:34:56Z into a lexicographically sortable token: 20260110T123456Z
    If parsing fails, returns a best-effort sanitized string.
    """
    text = started_at_iso.strip()
    if not text:
        return "unknown"

    # Common format: YYYY-MM-DDTHH:MM:SSZ
    if len(text) >= 20 and text[4] == "-" and text[7] == "-" and "T" in text:
        try:
            date_part, time_part = text.split("T", 1)
            yyyy, mm, dd = date_part.split("-", 2)
            hh = time_part[0:2]
            mi = time_part[3:5]
            ss = time_part[6:8]
            suffix = "Z" if text.endswith("Z") else ""
            return f"{yyyy}{mm}{dd}T{hh}{mi}{ss}{suffix}"
        except Exception:
            pass

    sanitized = "".join(ch for ch in text if ch.isalnum() or ch in {"T", "Z", "_"})
    return sanitized or "unknown"


def make_index_key(*, signature_hash: str, started_at: str, run_id: str) -> str:
    sortable = _sortable_started_at(started_at)
    return f"index/by_signature/{signature_hash}/{sortable}_{run_id}.json"


def canonicalize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize metric keys so baselines work even if internal naming changes

    Canonical keys used by remote index:
      - duration_seconds
      - peak_memory_bytes
      - total_read_bytes
      - total_write_bytes
      - peak_write_rate_bytes_per_second
      - extra_metrics (dict)
    """
    output: Dict[str, Any] = {}

    def pick(*keys: str) -> Any:
        for k in keys:
            if k in metrics:
                return metrics[k]
        return None

    output["duration_seconds"] = pick("duration_seconds", "duration_s")
    output["peak_memory_bytes"] = pick("peak_memory_bytes", "peak_rss_bytes")
    output["total_read_bytes"] = pick("total_read_bytes")
    output["total_write_bytes"] = pick("total_write_bytes")
    output["peak_write_rate_bytes_per_second"] = pick(
        "peak_write_rate_bytes_per_second",
        "peak_write_rate_bytes_s",
    )

    extra = metrics.get("extra_metrics")
    output["extra_metrics"] = extra if isinstance(extra, dict) else {}

    return output


def serialize_index_record(record: RunIndexRecord) -> bytes:
    payload = {
        "run_id": record.run_id,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "status": record.status,
        "exit_code": record.exit_code,
        "signal": record.signal,
        "signature_hash": record.signature_hash,
        "signature_label": record.signature_label,
        "tags": record.tags,
        "metrics": record.metrics,
        "artifacts_prefix": record.artifacts_prefix,
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def parse_index_record(data: bytes) -> RunIndexRecord:
    obj = json.loads(data.decode("utf-8"))
    return RunIndexRecord(
        run_id=str(obj["run_id"]),
        started_at=str(obj["started_at"]),
        ended_at=obj.get("ended_at"),
        status=str(obj["status"]),
        exit_code=obj.get("exit_code"),
        signal=obj.get("signal"),
        signature_hash=str(obj["signature_hash"]),
        signature_label=obj.get("signature_label"),
        tags=dict(obj.get("tags") or {}),
        metrics=dict(obj.get("metrics") or {}),
        artifacts_prefix=str(obj.get("artifacts_prefix") or ""),
    )
