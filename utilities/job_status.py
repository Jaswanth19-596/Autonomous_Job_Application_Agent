"""Workbook status-update tool exposed to the workflow."""

from pathlib import Path

from langchain_core.tools import tool

from workbook.job_identity import linkedin_job_id
from workbook.jobs_workbook import find_job_rows_by_canonical_url, update_job_row
from workbook.storage import workbook_exists


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_EXCEL_PATH = _PROJECT_ROOT / "data" / "applied_jobs.xlsx"


def _resolve_job_id(job_id: str) -> str:
    """Accept a spreadsheet ID, LinkedIn URL, or a unique stored job URL."""
    candidate = str(job_id or "").strip()
    if not candidate:
        raise ValueError("A job ID or job URL is required.")

    linked_in_id = linkedin_job_id(candidate)
    if linked_in_id:
        return linked_in_id

    if candidate.startswith(("http://", "https://")):
        matches = find_job_rows_by_canonical_url(_EXCEL_PATH, candidate)
        if not matches:
            raise ValueError(f"No job matches URL '{candidate}'.")
        if len(matches) > 1:
            raise ValueError(
                f"Job URL '{candidate}' is ambiguous: {len(matches)} rows match it."
            )
        return str(matches[0].get("id") or "").strip()

    return candidate


@tool
def update_job_status(
    job_id: str,
    application_status: str,
    status: str | None = None,
    reason: str | None = None,
) -> dict[str, str]:
    """Update one job's application status in data/applied_jobs.xlsx.

    `job_id` may be the workbook ID, a LinkedIn job URL, or another unique job
    URL stored in the workbook. Optional status text records the application
    result, while reason records why a job was not successfully applied to.
    """
    if not workbook_exists(_EXCEL_PATH):
        return {"error": "No jobs file found. Run the job fetcher first."}

    application_status_value = str(application_status or "").strip()
    if not application_status_value:
        return {"error": "An application status is required."}

    try:
        resolved_job_id = _resolve_job_id(job_id)
        if not resolved_job_id:
            return {"error": "The matching job does not have a workbook ID."}

        updates = {"application_status": application_status_value}
        if status and status.strip():
            updates["status"] = status.strip()
        if reason and reason.strip():
            updates["reason"] = reason.strip()
        update_job_row(_EXCEL_PATH, resolved_job_id, updates)
    except Exception as exc:
        return {"error": f"Could not update job status: {exc}"}

    return {
        "job_id": resolved_job_id,
        "application_status": application_status_value,
        "message": "Job status updated.",
    }
