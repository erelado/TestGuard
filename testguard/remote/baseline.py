from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from testguard.remote.uri import RemoteUri, parse_remote_uri
from testguard.remote.index import parse_index_record
from testguard.remote.object_store.base import ObjectStore
from testguard.remote.object_store.s3_store import S3ObjectStore


@dataclass(frozen=True, kw_only=True)
class RemoteBaseline:
    baseline_run_id: str
    metrics: Dict[str, object]


def _join_prefix(prefix: str, key: str) -> str:
    prefix = prefix.strip().strip("/")
    if not prefix:
        return key.lstrip("/")
    return f"{prefix}/{key.lstrip('/')}"


def _open_object_store(remote: RemoteUri) -> tuple[ObjectStore, str]:
    if remote.scheme == "s3":
        return S3ObjectStore(bucket=remote.bucket), remote.prefix
    raise ValueError(f"Unsupported remote scheme: {remote.scheme}")


def _tags_match(*, candidate: Dict[str, str], required: Dict[str, str]) -> bool:
    """required keys must exist and match exactly, extra keys in candidate are fine"""
    for key, value in required.items():
        if candidate.get(key) != value:
            return False
    return True


class RemoteBaselineSelector:
    """
    Select a baseline run from the remote index

    Default policy:
      - choose the latest OK run for the same signature_hash
      - optionally require some tags to match (recommended for CI stability)
    """

    def __init__(self, *, remote_uri: str) -> None:
        self._remote = parse_remote_uri(remote_uri=remote_uri)
        self._store, self._root_prefix = _open_object_store(self._remote)

    def find_latest_ok_baseline(
            self,
            *,
            signature_hash: str,
            exclude_run_id: str,
            required_tags: Dict[str, str],
            max_scan: int = 50,
    ) -> Optional[RemoteBaseline]:
        index_prefix = _join_prefix(self._root_prefix, f"index/by_signature/{signature_hash}/")

        keys = sorted(self._store.list_keys(prefix=index_prefix), reverse=True)
        if not keys:
            return None

        scanned = 0
        for key in keys:
            if scanned >= max_scan:
                return None
            scanned += 1

            record = parse_index_record(self._store.get_bytes(key=key))
            if record.run_id == exclude_run_id:
                continue

            if record.status != "OK":
                continue

            if record.exit_code not in (0, None):
                continue

            if required_tags and (not _tags_match(candidate=record.tags, required=required_tags)):
                continue

            return RemoteBaseline(
                baseline_run_id=record.run_id,
                metrics=dict(record.metrics),
            )

        return None
