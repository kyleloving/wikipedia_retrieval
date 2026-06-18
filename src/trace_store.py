"""Persist run traces under artifacts/runs/<run_id>/.

Layout:
    artifacts/runs/<run_id>/
        manifest.json
        traces/
            trace_000.json
            trace_001.json
            ...

Traces are plain dicts written as readable JSON. Grading reads these later
without re-running Claude.
"""

import json
import os
from datetime import datetime, timezone

ARTIFACTS_DIR = os.path.join("artifacts", "runs")


def new_run_id() -> str:
    """A sortable UTC-timestamp run id, e.g. 20260616T142530Z."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_dir(run_id: str) -> str:
    return os.path.join(ARTIFACTS_DIR, run_id)


def start_run(run_id: str = None) -> str:
    """Create the run directory (and traces/ subdir); return the run id."""
    run_id = run_id or new_run_id()
    os.makedirs(os.path.join(run_dir(run_id), "traces"), exist_ok=True)
    return run_id


def save_trace(run_id: str, trace: dict) -> str:
    """Write one trace as the next trace_NNN.json; return its path."""
    traces_dir = os.path.join(run_dir(run_id), "traces")
    os.makedirs(traces_dir, exist_ok=True)
    index = len([n for n in os.listdir(traces_dir) if n.endswith(".json")])
    path = os.path.join(traces_dir, f"trace_{index:03d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2, ensure_ascii=False, default=str)
    return path


def write_manifest(run_id: str, manifest: dict) -> str:
    """Write manifest.json for the run; return its path."""
    path = os.path.join(run_dir(run_id), "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)
    return path
