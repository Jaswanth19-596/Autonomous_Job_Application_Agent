"""Persist end-to-end application timings separately from verbose node logs."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from utilities.application_metrics import empty_metrics
from utilities.diagnostics import log_workflow_event

TIMING_LOG_PATH = Path(__file__).resolve().parents[1] / "logs/application_timings.jsonl"


def start_application_timing():
    # Epoch time survives checkpoint restoration in a different process.
    return {
        "application_started_at": time.time(),
        "application_elapsed_seconds": None,
        "application_metrics": empty_metrics(),
    }


def finish_application_timing(
    state,
    outcome,
    *,
    workbook_path=None,
    workbook_updates=None,
):
    """Record a completed attempt and persist all workbook fields in one update."""
    started = state.get("application_started_at")
    if started is None:
        if workbook_path is not None and workbook_updates:
            from workbook.jobs_workbook import update_job_row

            details = state.get("job_details") or {}
            if details.get("id"):
                update_job_row(
                    Path(workbook_path),
                    str(details["id"]),
                    dict(workbook_updates),
                )
        return {}
    ended = time.time()
    elapsed = round(max(0.0, ended - started), 3)
    # Validation can reject a job before its details replace the previous job's.
    details = {} if state.get("validation_phase") == "NOT_ELIGIBLE" else (state.get("job_details") or {})

    # Pull accumulated metrics (gracefully handle legacy checkpoints)
    metrics = state.get("application_metrics") or {}

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
        # --- new metric fields ---
        "total_input_tokens": metrics.get("total_input_tokens", 0),
        "total_output_tokens": metrics.get("total_output_tokens", 0),
        "total_tokens": metrics.get("total_tokens", 0),
        "llm_call_count": metrics.get("llm_call_count", 0),
        "total_cost_usd": metrics.get("total_cost_usd", 0.0),
        "tool_call_count": metrics.get("tool_call_count", 0),
        "node_timings": metrics.get("node_timings", {}),
        "human_intervention_count": metrics.get("human_intervention_count", 0),
        "retry_count": metrics.get("retry_count", 0),
        "applied_at": metrics.get("applied_at"),
    }

    # Persist status, timing, and metrics together so a Drive-backed workbook is
    # downloaded and uploaded only once for the completed application.
    if workbook_path is not None and record["job_id"]:
        from workbook.jobs_workbook import update_job_row

        updates = dict(workbook_updates or {})
        updates.update({
            "application_elapsed_seconds": elapsed,
            "application_started_at": record["started_at"],
            "application_finished_at": record["finished_at"],
            "application_cost_usd": record["total_cost_usd"],
            "application_tool_calls": record["tool_call_count"],
            "llm_call_count": record["llm_call_count"],
            "total_tokens": record["total_tokens"],
            "human_intervention_count": record["human_intervention_count"],
            "applied_at": record["applied_at"] or "",
        })
        update_job_row(Path(workbook_path), str(record["job_id"]), updates)

    TIMING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TIMING_LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    log_workflow_event("Application finished", **record)
    return {
        "application_started_at": None,
        "application_elapsed_seconds": elapsed,
        "application_metrics": {},
    }
