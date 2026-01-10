# TestGuard/remote/

This package implements TestGuard's remote storage layer.

It uploads run artifacts to an object store (example: AWS S3) and writes a small per-run index record that enables baseline selection across machines (for example, multiple EC2 runners).

## What remote does

Remote adds two capabilities:

1) Upload the artifacts directory of a run to object storage.
2) Write a small index record per run, organized by `signature_hash`, so any machine can later find a baseline run without having the local SQLite DB.

Remote is intentionally simple. It does not maintain a database, it stores immutable objects and relies on list + sort for baseline lookup.

## Remote URI

Remote destinations are configured via a URI, for example:

- `s3://test_run_logs/testguard`

This means:

- bucket: `test_run_logs`
- prefix: `testguard`

All objects will be stored under that prefix.

Implementation: `uri.py`

## Storage layout in S3

Given `s3://test_run_logs/testguard`, remote writes two namespaces.

### 1) Artifacts (full run directory)

All files under the local run directory are uploaded to:

- `testguard/runs/<run_id>/<relative_path>`

Example:

- local: `/var/lib/testguard/runs/abc123/run_report.json`
- remote key: `testguard/runs/abc123/run_report.json`

Implementation: `sync.py` (`upload_run_directory`)

### 2) Index records (small JSON files)

Each completed run produces one JSON index record stored under:

- `testguard/index/by_signature/<signature_hash>/<sortable_started_at>_<run_id>.json`

Example:

- `testguard/index/by_signature/4d2e.../20260110T123456Z_abc123.json`

The filename is designed so lexicographic sort matches time order. `run_id` is included to prevent collisions.

Implementation: `index.py` (`make_index_key`, `_sortable_started_at`), written by `sync.py` (`upload_index_record`)

## Index record content

The index record is the only thing needed for baseline selection. It contains:

- `run_id`, `started_at`, `ended_at`
- `status`, `exit_code`, `signal`
- `signature_hash`
- `tags` (dict of strings)
- canonicalized metrics (stable field names even if internal metric names change)
- `artifacts_prefix`, which points to `runs/<run_id>/`

Implementation: `index.py` (`canonicalize_metrics`)

## Baseline selection across machines

Baseline selection is remote-first, and works even on a fresh machine with no local history.

The algorithm:

1) List all index keys under:
   `index/by_signature/<signature_hash>/`
2) Sort keys newest-first (the timestamp prefix makes this correct).
3) Scan entries until you find a candidate that:
   - is not the current `run_id`
   - has `status == "OK"`
   - has exit code 0 (or exit code is missing)
   - matches required tags (see next section)
4) Return that run_id and its metrics as the baseline.

Implementation: `baseline.py` (`RemoteBaselineSelector.find_latest_ok_baseline`)

### Tag filtering

Signature hashes are often too broad for CI. Tags provide a stable way to compare like-for-like runs.

Typical tags you should write per run:

- `branch`: `main`, `pr-123`, etc
- `suite`: `unit`, `integration`, `e2e`
- `python`: `3.11`
- `os`: `al2023`

Baseline selection supports "required tags" semantics:

- the baseline must include all required tags with equal values
- extra tags on the baseline are allowed

This enables policies like:

- PR runs compare to last successful `main` baseline
- unit tests do not baseline against e2e runs
- python 3.12 runs do not baseline against python 3.11 runs

## Concurrency and multiple EC2 runners

Remote is safe when many machines upload simultaneously.

Why it works:

- Artifacts are stored under `runs/<run_id>/...`, and `run_id` is unique.
- Index keys include `<sortable_started_at>_<run_id>.json`, so even runs that start at the same second cannot overwrite each other.

What happens during simultaneous runs:

- If machines A and B start at the same time, both will usually pick the same last known OK baseline (the latest completed run).
- If A finishes first and uploads, then a later run on machine C can pick A as the new baseline.
- A run does not change its baseline mid-run, baseline selection happens at the time the run begins (or before diffing).


## IAM permissions (S3)

Assume:
- 3 EC2 instances run tests on demand.
- S3 bucket: `test_run_logs`
- Remote URI: `s3://test_run_logs/testguard`

Then the EC2 instance role needs:
- `s3:PutObject` on test_run_logs/testguard/*
- `s3:GetObject` on test_run_logs/testguard/*
- `s3:ListBucket` on test_run_logs (scoped to prefix `testguard/index/by_signature/` is ideal)

Baseline selection needs `ListBucket` and `GetObject` for index records.  Uploads need `PutObject`.