from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True, kw_only=True)
class LocalRetentionPolicy:
    """
    Local retention policy for run data.

    Supported values (case-insensitive):
      - keep
      - delete-after-upload
      - keep-last:N
      - keep-days:N

    Note:
      * Any policy that deletes data is gated by `remote_upload_succeeded`. If upload failed, we keep everything
        to avoid losing data
    """
    mode: str  # "keep" | "delete-after-upload" | "keep-last" | "keep-days"
    keep_last: Optional[int] = None
    keep_days: Optional[int] = None


@dataclass(frozen=True, kw_only=True)
class RetentionRun:
    run_id: str
    finished_at_epoch: float
    artifacts_dir: Optional[Path]


_KEEP_LAST_RE = re.compile(r"^keep[-_ ]?last\s*[:=]\s*(\d+)$")
_KEEP_DAYS_RE = re.compile(r"^keep[-_ ]?days\s*[:=]\s*(\d+)$")


def parse_local_retention(policy_text: Optional[str], *, default_mode: str) -> LocalRetentionPolicy:
    text = (policy_text or "").strip().lower()
    if not text:
        return LocalRetentionPolicy(mode=default_mode)

    if text in {"keep", "infinite"}:
        return LocalRetentionPolicy(mode="keep")

    if text in {"delete-after-upload", "delete_after_upload", "delete"}:
        return LocalRetentionPolicy(mode="delete-after-upload")

    match_last = _KEEP_LAST_RE.match(text)
    if match_last:
        keep_last = int(match_last.group(1))
        if keep_last <= 0:
            raise ValueError(f"keep-last must be >= 1, got: {keep_last}")
        return LocalRetentionPolicy(mode="keep-last", keep_last=keep_last)

    match_days = _KEEP_DAYS_RE.match(text)
    if match_days:
        keep_days = int(match_days.group(1))
        if keep_days <= 0:
            raise ValueError(f"keep-days must be >= 1, got: {keep_days}")
        return LocalRetentionPolicy(mode="keep-days", keep_days=keep_days)

    raise ValueError(f"Unsupported local retention policy: {policy_text!r}")


def apply_local_retention_for_run(
    *,
    store: "SQLiteRunStore",
    run_id: str,
    run_directory: Path,
    retention: LocalRetentionPolicy,
    remote_upload_succeeded: bool,
) -> None:
    if retention.mode == "keep":
        return

    # Any policy that deletes data is gated behind successful remote upload.
    if not remote_upload_succeeded:
        return

    if retention.mode == "delete-after-upload":
        _delete_run_everything_best_effort(store=store, run_id=run_id, run_directory=run_directory)
        return

    # For keep-last / keep-days we prune OTHER runs (and possibly current run only if it is outside policy,
    # which should not happen in normal flow where the current run is among the newest).
    if retention.mode == "keep-last":
        assert retention.keep_last is not None
        _prune_keep_last(
            store=store,
            keep_last=retention.keep_last,
            fallback_run_dir_parent=run_directory.parent,
            always_keep_run_id=run_id,
        )
        return

    if retention.mode == "keep-days":
        assert retention.keep_days is not None
        _prune_keep_days(
            store=store,
            keep_days=retention.keep_days,
            fallback_run_dir_parent=run_directory.parent,
        )
        return

    raise AssertionError(f"Unhandled retention mode: {retention.mode}")


def _prune_keep_last(
    *,
    store: "SQLiteRunStore",
    keep_last: int,
    fallback_run_dir_parent: Path,
    always_keep_run_id: str,
) -> None:
    runs = list(_list_runs_for_retention(store))
    # newest first
    runs.sort(key=lambda r: r.finished_at_epoch, reverse=True)

    keep_ids = {r.run_id for r in runs[:keep_last]}
    keep_ids.add(always_keep_run_id)

    for run in runs:
        if run.run_id in keep_ids:
            continue
        _delete_run_by_metadata_best_effort(store=store, run=run, fallback_run_dir_parent=fallback_run_dir_parent)


def _prune_keep_days(
    *,
    store: "SQLiteRunStore",
    keep_days: int,
    fallback_run_dir_parent: Path,
) -> None:
    cutoff_epoch = time.time() - (keep_days * 24 * 60 * 60)

    runs = list(_list_runs_for_retention(store))
    for run in runs:
        if run.finished_at_epoch >= cutoff_epoch:
            continue
        _delete_run_by_metadata_best_effort(store=store, run=run, fallback_run_dir_parent=fallback_run_dir_parent)


def _delete_run_by_metadata_best_effort(
    *,
    store: "SQLiteRunStore",
    run: RetentionRun,
    fallback_run_dir_parent: Path,
) -> None:
    artifacts_dir = run.artifacts_dir
    if artifacts_dir is None:
        # Fallback convention: <parent>/<run_id>
        candidate = fallback_run_dir_parent / run.run_id
        if candidate.exists():
            artifacts_dir = candidate

    if artifacts_dir is not None:
        _delete_directory_best_effort(artifacts_dir)

    store.delete_run(run_id=run.run_id)


def _delete_run_everything_best_effort(
    *,
    store: "SQLiteRunStore",
    run_id: str,
    run_directory: Path,
) -> None:
    if run_directory.exists():
        _delete_directory_best_effort(run_directory)

    store.delete_run(run_id=run_id)


def _delete_directory_best_effort(directory: Path) -> None:
    # Delete files first, then directories, best effort.
    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_file() or path.is_symlink():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass

    try:
        directory.rmdir()
    except OSError:
        pass


def _list_runs_for_retention(store: "SQLiteRunStore") -> Iterable[RetentionRun]:
    """
    Expected SQLiteRunStore API:

      store.list_runs_for_retention() -> Iterable[RetentionRun]

    Where each run includes:
      - run_id
      - finished_at_epoch (float seconds since epoch, used for ordering and keep-days cutoff)
      - artifacts_dir (optional Path, can be None)

    If you do not have this method yet, add it to SQLiteRunStore by querying your runs table.
    """
    list_method = getattr(store, "list_runs_for_retention", None)
    if list_method is None:
        raise NotImplementedError(
            "SQLiteRunStore.list_runs_for_retention() is required for keep-last / keep-days pruning."
        )

    runs = list_method()
    for run in runs:
        yield run
