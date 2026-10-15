"""Read-only pending and invalid job context exposed to the language model."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
from langchain_core.tools import tool
from workbook.storage import workbook_file


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_WORKBOOKS = {
    "pending": _PROJECT_ROOT / "data" / "pending_jobs.xlsx",
    "invalid": _PROJECT_ROOT / "data" / "invalid_jobs.xlsx",
}
_MAX_PAGE_SIZE = 50


def _load_records(source: str) -> list[dict[str, str]]:
    """Load one small context workbook while preserving its workbook fields."""
    path = _WORKBOOKS[source]
    with workbook_file(path) as local:
        if not local.exists():
            raise FileNotFoundError(f"{path.name} does not exist.")
        dataframe = pd.read_excel(local, dtype=str).fillna("")
    records = dataframe.to_dict(orient="records")
    return [{"job_source": source, **record} for record in records]


@tool
def get_pending_or_invalid_jobs(
    job_type: Literal["pending", "invalid", "all"],
    query: str | None = None,
    n: int = 10,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict:
    """Search pending or invalid job records for conversational context.

    Choose ``pending`` for jobs waiting to be processed, ``invalid`` for jobs
    rejected by validation, or ``all`` when the question needs both. Pending
    records contain their URL and collection time; invalid records contain the
    URL and rejection reason. This tool is read-only and never starts an
    application.
    """
    if n < 1:
        return {"error": "n must be at least 1."}
    if offset < 0:
        return {"error": "offset cannot be negative."}

    sources = ("pending", "invalid") if job_type == "all" else (job_type,)
    try:
        records = [record for source in sources for record in _load_records(source)]
    except Exception as exc:
        return {"error": f"Could not read job context: {exc}"}

    query_text = str(query or "").strip().casefold()
    if query_text:
        records = [
            record
            for record in records
            if any(query_text in str(value).casefold() for value in record.values())
        ]

    total_matching = len(records)
    page = records[offset : offset + min(n, _MAX_PAGE_SIZE)]
    if fields:
        selected_fields = set(fields) | {"job_source"}
        page = [
            {field: value for field, value in record.items() if field in selected_fields}
            for record in page
        ]

    return {
        "job_type": job_type,
        "total_matching": total_matching,
        "returned_count": len(page),
        "offset": offset,
        "jobs": page,
    }
