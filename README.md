# TestGuard

A CLI wrapper that runs any command under a lightweight "resource safety harness". 
It records what happened, samples system and process metrics while the command runs, enforces thresholds 
(kill on panic), and then writes a small set of artifacts you can diff against prior runs.

It is built to answer a very practical question: “Did this test run (or script, or build) suddenly become heavier, 
slower, or riskier for my machine?”

## What it is

1. Runs your command in its own process group (so it can stop the entire process tree if needed).
2. Samples resource metrics during the run via pluggable collectors (memory today, more coming).
3. Enforces thresholds (warn or kill on panic), then writes a report you can diff against a baseline run.

All data is stored locally in `~/.testguard/`:
- A SQLite DB for run metadata, samples, events, and summaries
- Per-run artifacts in `~/.testguard/runs/<run_id>/` (JSON + Markdown), ready to upload as CI artifacts if you want

The code is split into focused modules to keep it easy to extend:
- `engine/` orchestrates the run lifecycle
- `monitor/` schedules sampling and merges collector fragments
- `monitor/collectors/` provides OS-specific metric sources
- `governor/` decides warn vs panic based on thresholds
- `analysis/` computes metrics and diffs vs a baseline
- `report/` renders terminal output and artifacts
- `store/` persists everything in SQLite

## Install

### Recommended (uv)

```bash
uv venv
uv pip install -e .
```

Run the CLI through uv:

```bash
uv run testguard doctor
uv run testguard run python -c "print('ok')"
```

### Alternative (pip)

```bash
python -m pip install -e .
```

then

```bash
testguard doctor
testguard run -- python -c "print('ok')"
```

## Optional extras

TestGuard is stdlib-only by default. Optional extras add extra collectors:

```bash
uv pip install -e ".[psutil]"
uv pip install -e ".[pynvml]"

# or with pip
python -m pip install -e ".[psutil]"
python -m pip install -e ".[pynvml]"
```

## Quick start

```bash
testguard doctor
testguard run -- /bin/true
testguard run -- python -c "print('hello from child')"
```

## Build a distribution (sdist + wheel)

```bash
uv build
ls -lah dist/

# Install the built wheel locally for a sanity check:
# uv venv
# uv pip install dist/testguard-*.whl
# testguard doctor
```

## Run data location

- Base dir: `~/.testguard/`
- DB: `~/.testguard/runs.db`
- Artifacts: `~/.testguard/runs/<run_id>/`