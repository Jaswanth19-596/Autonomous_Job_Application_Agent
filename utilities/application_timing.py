"""Persist end-to-end application timings separately from verbose node logs."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from utilities.diagnostics import log_workflow_event

TIMING_LOG_PATH = Path(__file__).resolve().parents[1] / "logs/application_timings.jsonl"


def start_application_timing():
    # Epoch time survives checkpoint restoration in a different process.
    return {"application_started_at": time.time(), "application_elapsed_seconds": None}


def finish_application_timing(state, outcome, *, workbook_path=None):
    """Record a completed attempt; older checkpoints may have no start time."""
    started = state.get("application_started_at")
    if started is None:
        return {}
    ended = time.time()
    elapsed = round(max(0.0, ended - started), 3)
    # Validation can reject a job before its details replace the previous job's.
    details = {} if state.get("validation_phase") == "NOT_ELIGIBLE" else (state.get("job_details") or {})
    record = {
        "job_url": state.get("job_url"),
        "job_id": details.get("id"),
        "job_title": details.get("title"),
        "company": details.get("companyName"),
        "outcome": outcome,
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "finished_at": datetime.fromtimestamp(ended, timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "includes_human_wait": True,
    }
    if workbook_path is not None and record["job_id"]:
        from workbook.jobs_workbook import update_job_row

        update_job_row(Path(workbook_path), str(record["job_id"]), {
            "application_elapsed_seconds": elapsed,
            "application_started_at": record["started_at"],
            "application_finished_at": record["finished_at"],
        })
    TIMING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TIMING_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    log_workflow_event("Application finished", **record)
    return {"application_started_at": None, "application_elapsed_seconds": elapsed}
