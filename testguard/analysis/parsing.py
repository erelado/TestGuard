from __future__ import annotations

import json
from typing import Any, Dict, Optional

from testguard.store.store import SampleRecord


def parse_sample_payload(sample: SampleRecord) -> Dict[str, Any]:
    try:
        return json.loads(sample.payload_json)
    except Exception:
        return {}


def extract_int(payload: Dict[str, Any], key: str) -> Optional[int]:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_float(payload: Dict[str, Any], key: str) -> Optional[float]:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pick_observed_memory_bytes(payload: Dict[str, Any]) -> Optional[int]:
    """
    Returns the best available "current memory usage" observation in bytes.

    Preference order:
    1) cgroup v2 memory.current (when the run is inside a cgroup and we can sample it)
    2) process RSS bytes (best-effort approximation)
    """
    for key in ("cgroup_memory_current_bytes", "rss_bytes"):
        value = extract_int(payload, key)
        if value is not None and value >= 0:
            return value
    return None


def monotonic_timestamp_seconds(sample: SampleRecord) -> Optional[float]:
    # Stored samples use `ts_monotonic`. Keep this helper for readability.
    value = getattr(sample, "ts_monotonic", None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
