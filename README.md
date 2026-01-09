# TestGuard (MVP in progress)

Linux-first, local-first CLI wrapper that runs any command and records run history locally.
This step ships a working CLI, a local SQLite run registry, and a `doctor` command.
Monitoring, thresholds, post-mortems, diffs, and recommendations arrive in later steps.

## Install (editable)

```bash
python -m pip install -e .
```

## Quick start

```bash
testguard doctor
testguard run -- /bin/true
testguard run -- python -c "print('hello from child')"
```

## Run data location

- Base dir: ~/.testguard/
- DB: ~/.testguard/runs.db
- Artifacts: ~/.testguard/runs/<run_id>/