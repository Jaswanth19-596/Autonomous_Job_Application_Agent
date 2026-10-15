"""Job-workbook lookup tool exposed to the language model."""

from pathlib import Path

import pandas as pd
from langchain_core.tools import tool

from workbook.job_identity import canonical_job_id
from workbook.jobs_workbook import get_job_row
from workbook.storage import workbook_exists, workbook_file


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_EXCEL_PATH = _PROJECT_ROOT / "data" / "applied_jobs.xlsx"

_HEAVY_JOB_COLUMNS = {
    "descriptionHtml",
    "descriptionText",
    "companyDescription",
    "companyAddress",
    "companyLogo",
    "inputUrl",
    "companySlogan",
    "companyLinkedinUrl",
    "trackingId",
    "refId",
    "jobPosterName",
    "jobPosterTitle",
    "jobPosterPhoto",
    "jobPosterProfileUrl",
    "companyEmployeesCount",
    "benefits",
}


@tool
def get_applied_jobs(
    filters: list[str] | None = None,
    query: str | None = None,
    n: int = 10,
    offset: int = 0,
    job_id: str | None = None,
    sort_by: str = "postedAt",
    order: str = "asc",
    fields: list[str] | None = None,
    include_description_snippet: bool = False,
) -> dict:
    """Fetch and search jobs from data/applied_jobs.xlsx with filtering and pagination."""
    if not workbook_exists(_EXCEL_PATH):
        return {"error": "No jobs file found. Run the job fetcher first."}

    target_id = str(job_id or "").strip()
    if target_id:
        try:
            record = get_job_row(_EXCEL_PATH, target_id)
        except Exception as exc:
            return {"error": f"Could not read record: {exc}"}

        if not record:
            return {"total_matching": 0, "jobs": []}

        record["id"] = canonical_job_id(record)
        if fields:
            record = {key: value for key, value in record.items() if key in fields or key == "id"}
        else:
            record = {key: value for key, value in record.items() if key not in _HEAVY_JOB_COLUMNS}
        return {"total_matching": 1, "jobs": [record]}

    try:
        with workbook_file(_EXCEL_PATH) as local:
            dataframe = pd.read_excel(local, dtype=str)
    except Exception as exc:
        return {"error": f"Could not read file: {exc}"}

    if dataframe.empty:
        return {"total_matching": 0, "jobs": []}

    if "application_status" not in dataframe.columns:
        dataframe["application_status"] = "Not Applied"
    dataframe["application_status"] = (
        dataframe["application_status"]
        .fillna("Not Applied")
        .astype(str)
        .str.strip()
        .replace({"nan": "Not Applied", "None": "Not Applied", "": "Not Applied"})
    )

    if filters and not any(str(item).upper() == "ALL" for item in filters):
        filter_set = {str(item).strip() for item in filters if item}
        dataframe = dataframe[dataframe["application_status"].isin(filter_set)]

    if query:
        searchable_columns = [
            column
            for column in ("title", "companyName", "location")
            if column in dataframe.columns
        ]
        mask = dataframe[searchable_columns].apply(
            lambda row: row.astype(str).str.lower().str.contains(query.lower(), na=False).any(),
            axis=1,
        )
        dataframe = dataframe[mask]

    if sort_by in dataframe.columns:
        if sort_by == "postedAt":
            posted_at = pd.to_datetime(dataframe["postedAt"], errors="coerce", utc=True)
            queue_ready_at = (
                pd.to_datetime(dataframe["queue_ready_at"], errors="coerce", utc=True)
                if "queue_ready_at" in dataframe.columns
                else pd.Series(pd.NaT, index=dataframe.index)
            )
            fetched_at = (
                pd.to_datetime(dataframe["fetched_at"], errors="coerce", utc=True)
                if "fetched_at" in dataframe.columns
                else pd.Series(pd.NaT, index=dataframe.index)
            )
            dataframe["_sort_key"] = posted_at.fillna(queue_ready_at).fillna(fetched_at)
        else:
            dataframe["_sort_key"] = dataframe[sort_by]
        dataframe.sort_values(
            "_sort_key", ascending=order.lower() == "asc", kind="stable", inplace=True
        )
        dataframe.drop(columns=["_sort_key"], inplace=True)

    total_matching = len(dataframe)
    records = dataframe.iloc[offset : offset + n].copy().fillna("").to_dict(orient="records")
    jobs = []
    for record in records:
        record["id"] = canonical_job_id(record)
        if include_description_snippet and "descriptionText" in record:
            description = record["descriptionText"]
            record["description_snippet"] = (
                f"{description[:300]}..." if len(description) > 300 else description
            )
        if fields:
            selected = {key: record[key] for key in fields if key in record}
            selected["id"] = record["id"]
            jobs.append(selected)
        else:
            jobs.append({key: value for key, value in record.items() if key not in _HEAVY_JOB_COLUMNS})

    return {
        "total_matching": total_matching,
        "returned_count": len(jobs),
        "offset": offset,
        "jobs": jobs,
    }
