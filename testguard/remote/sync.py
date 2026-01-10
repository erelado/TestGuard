from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from testguard.remote.index import RunIndexRecord, canonicalize_metrics, make_index_key, serialize_index_record
from testguard.remote.object_store.base import ObjectStore
from testguard.remote.object_store.s3_store import S3ObjectStore
from testguard.remote.uri import RemoteUri, parse_remote_uri


@dataclass(frozen=True, kw_only=True)
class RemoteSyncResult:
    uploaded: bool
    message: str


def _join_prefix(prefix: str, key: str) -> str:
    prefix = prefix.strip().strip("/")
    if not prefix:
        return key.lstrip("/")
    return f"{prefix}/{key.lstrip('/')}"


def _content_type_for_path(path: Path) -> Optional[str]:
    name = path.name.lower()
    if name.endswith(".json"):
        return "application/json"
    if name.endswith(".md"):
        return "text/markdown"
    if name.endswith(".txt"):
        return "text/plain"
    return None


def _open_object_store(remote: RemoteUri) -> Tuple[ObjectStore, str]:
    """Returns (store, root_prefix). root_prefix may be '.'"""
    if remote.scheme == "s3":
        return S3ObjectStore(bucket=remote.bucket), remote.prefix
    raise ValueError(f"Unsupported remote scheme: {remote.scheme}")


class RemoteRunSync:
    """Upload local per-run artifacts plus a small index record to a remote object store."""

    def __init__(self, *, remote_uri: str) -> None:
        self._remote = parse_remote_uri(remote_uri=remote_uri)
        self._store, self._root_prefix = _open_object_store(remote=self._remote)

    def upload_run_directory(self, *, run_id: str, local_run_directory: Path) -> RemoteSyncResult:
        if not local_run_directory.exists():
            return RemoteSyncResult(uploaded=False, message=f"local run directory missing: {local_run_directory}")

        remote_runs_prefix = _join_prefix(self._root_prefix, f"runs/{run_id}")

        # Upload everything in the run directory (small set of files today).
        for path in sorted(local_run_directory.rglob("*")):
            if path.is_dir():
                continue
            relative = path.relative_to(local_run_directory).as_posix()
            key = _join_prefix(remote_runs_prefix, relative)
            data = path.read_bytes()
            self._store.put_bytes(key=key, data=data, content_type=_content_type_for_path(path))

        return RemoteSyncResult(uploaded=True, message=f"uploaded runs/{run_id}")

    def upload_index_record(
            self,
            *,
            run_id: str,
            started_at: str,
            ended_at: Optional[str],
            status: str,
            exit_code: Optional[int],
            signal: Optional[int],
            signature_hash: str,
            signature_label: Optional[str],
            tags: Dict[str, str],
            metrics: Dict[str, Any],
    ) -> RemoteSyncResult:
        canonical_metrics = canonicalize_metrics(metrics)
        record = RunIndexRecord(
            run_id=run_id,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            exit_code=exit_code,
            signal=signal,
            signature_hash=signature_hash,
            signature_label=signature_label,
            tags=tags,
            metrics=canonical_metrics,
            artifacts_prefix=f"runs/{run_id}/",
        )

        key = make_index_key(signature_hash=signature_hash, started_at=started_at, run_id=run_id)
        key = _join_prefix(self._root_prefix, key)

        self._store.put_bytes(key=key, data=serialize_index_record(record), content_type="application/json")
        return RemoteSyncResult(uploaded=True, message=f"uploaded {key}")

    def push_from_run_report_json(
            self,
            *,
            run_directory: Path,
            run_report_json_path: Path,
    ) -> RemoteSyncResult:
        """
        Convenience helper: read the already-written run_report.json and push both artifacts and index.
        """
        report = json.loads(run_report_json_path.read_text(encoding="utf-8"))

        run_id = str(report["run_id"])
        started_at = str(report["started_at"])
        ended_at = report.get("ended_at")
        status = str(report["status"])
        exit_code = report.get("exit_code")
        signal = report.get("signal")
        signature_hash = str(report["signature_hash"])

        run_config = report.get("run_config") or {}
        if isinstance(run_config, str):
            try:
                run_config = json.loads(run_config)
            except Exception:
                run_config = {}

        signature_label = run_config.get("signature_label")
        tags = run_config.get("tags") or {}
        if not isinstance(tags, dict):
            tags = {}

        metrics = report.get("metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}

        upload_artifacts = self.upload_run_directory(run_id=run_id, local_run_directory=run_directory)
        if not upload_artifacts.uploaded:
            return upload_artifacts

        upload_index = self.upload_index_record(
            run_id=run_id,
            started_at=started_at,
            ended_at=ended_at,
            status=status,
            exit_code=exit_code,
            signal=signal,
            signature_hash=signature_hash,
            signature_label=signature_label,
            tags={str(k): str(v) for k, v in tags.items()},
            metrics=metrics,
        )
        if not upload_index.uploaded:
            return upload_index

        return RemoteSyncResult(uploaded=True, message="uploaded artifacts + index")
