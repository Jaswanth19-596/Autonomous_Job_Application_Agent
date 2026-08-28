from langchain_core.tools import tool
from pathlib import Path
import pandas as pd
from workbook.job_identity import canonical_job_id, canonical_job_url

_BASE_DIR = Path(__file__).resolve().parents[0]
_EXCEL_PATH = _BASE_DIR / "data" / "jobs.xlsx"

_HEAVY_JOB_COLUMNS = {
    "descriptionHtml", "descriptionText", "companyDescription",
    "companyAddress", "companyLogo", "inputUrl", "companySlogan",
    "companyLinkedinUrl", "trackingId", "refId", "jobPosterName",
    "jobPosterTitle", "jobPosterPhoto", "jobPosterProfileUrl",
    "companyEmployeesCount", "benefits",
}


@tool
def get_jobs(
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
    """Fetch and search jobs from data/jobs.xlsx with enhanced filtering and pagination.

    Args:
        filters: Optional status filters (e.g. ["Not Applied"], ["Applied"]).
        query: Case-insensitive search string to match against title, company, or location.
        n: Maximum number of jobs to return (pagination limit). Defaults to 10.
        offset: Number of records to skip (pagination offset). Defaults to 0.
        job_id: Exact job ID. Omit to search the broader queue.
        sort_by: Column to sort results by (e.g., 'postedAt', 'companyName', 'title'). Defaults to 'postedAt'.
        order: Direction to sort results ('asc' or 'desc'). Defaults to 'asc'.
        fields: Optional list of specific columns to return to minimize token usage.
        include_description_snippet: If True, adds a 300-character snippet of descriptionText.
    """
    if not _EXCEL_PATH.exists():
        return {"error": "No jobs file found. Run the job fetcher first."}

    # 1. Single Job Lookup Path
    target_id = str(job_id or "").strip()
    if target_id:
        try:
            from src.data.jobs_workbook import get_job_row, find_job_rows_by_canonical_url, update_job_row
            record = get_job_row(_EXCEL_PATH, target_id)
        except Exception as exc:
            return {"error": f"Could not read record: {exc}"}

        if not record:
            return {"total_matching": 0, "jobs": []}

        record["id"] = canonical_job_id(record)

        # Trim payload if specific fields requested
        if fields:
            record = {k: v for k, v in record.items() if k in fields or k == "id"}
        else:
            record = {k: v for k, v in record.items() if k not in _HEAVY_JOB_COLUMNS}

        return {"total_matching": 1, "jobs": [record]}

    # 2. DataFrame Load
    try:
        df = pd.read_excel(_EXCEL_PATH, dtype=str)
    except Exception as exc:
        return {"error": f"Could not read file: {exc}"}

    if df.empty:
        return {"total_matching": 0, "jobs": []}

    # 3. Clean Status Column
    if "application_status" not in df.columns:
        df["application_status"] = "Not Applied"

    df["application_status"] = (
        df["application_status"]
        .fillna("Not Applied")
        .astype(str)
        .str.strip()
        .replace({"nan": "Not Applied", "None": "Not Applied", "": "Not Applied"})
    )

    # 4. Filter by Status
    if filters and not any(str(f).upper() == "ALL" for f in filters):
        filter_set = {str(f).strip() for f in filters if f}
        df = df[df["application_status"].isin(filter_set)]

    # 5. Free-Text Search Filter
    if query:
        q = query.lower()
        searchable_cols = [c for c in ["title", "companyName", "location"] if c in df.columns]
        mask = df[searchable_cols].apply(
            lambda row: row.astype(str).str.lower().str.contains(q, na=False).any(), axis=1
        )
        df = df[mask]

    # 6. Sorting Logic
    if sort_by in df.columns:
        if sort_by == "postedAt":
            # Date priority parsing fallback
            p_date = pd.to_datetime(df["postedAt"], errors="coerce", utc=True) if "postedAt" in df.columns else pd.Series(pd.NaT, index=df.index)
            q_date = pd.to_datetime(df["queue_ready_at"], errors="coerce", utc=True) if "queue_ready_at" in df.columns else pd.Series(pd.NaT, index=df.index)
            f_date = pd.to_datetime(df["fetched_at"], errors="coerce", utc=True) if "fetched_at" in df.columns else pd.Series(pd.NaT, index=df.index)
            df["_sort_key"] = p_date.fillna(q_date).fillna(f_date)
        else:
            df["_sort_key"] = df[sort_by]

        df.sort_values("_sort_key", ascending=(order.lower() == "asc"), kind="stable", inplace=True)
        df.drop(columns=["_sort_key"], inplace=True)

    total_matching = len(df)

    # 7. Pagination
    paginated_df = df.iloc[offset : offset + n].copy()

    # 8. Column Projection & Formatting
    records = paginated_df.fillna("").to_dict(orient="records")
    out_records = []

    for record in records:
        record["id"] = canonical_job_id(record)

        if include_description_snippet and "descriptionText" in record:
            desc = record.get("descriptionText", "")
            record["description_snippet"] = desc[:300] + "..." if len(desc) > 300 else desc

        if fields:
            filtered_record = {k: record[k] for k in fields if k in record}
            filtered_record["id"] = record["id"]
            out_records.append(filtered_record)
        else:
            out_records.append({k: v for k, v in record.items() if k not in _HEAVY_JOB_COLUMNS})

    return {
        "total_matching": total_matching,
        "returned_count": len(out_records),
        "offset": offset,
        "jobs": out_records,
    }