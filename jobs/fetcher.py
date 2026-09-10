import os
import hashlib
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv

from workbook.job_identity import candidate_job_fingerprint, canonical_job_id, job_identity

load_dotenv()

# Determine project root and data directory
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXCEL_PATH = DATA_DIR / "applied_jobs.xlsx"


# Keep this schema in one place: every fetch removes all other workbook fields.
# The final five fields are populated later by the application workflow.
JOB_COLUMNS = (
    "id",
    "link",
    "title",
    "companyName",
    "location",
    "postedAt",
    "applyUrl",
    "salary",
    "descriptionText",
    "seniorityLevel",
    "employmentType",
    "application_status",
    "fetched_at",
    "jobboard",
)

APPLICATION_METRIC_COLUMNS = (
    "application_started_at",
    "application_finished_at",
    "application_duration_seconds",
    "application_tool_calls",
    "application_cost_usd",
)

_APPLICATION_STATUS_PRIORITY = {
    "Not Applied": 0,
    "Failed": 1,
    "Needs CAPTCHA": 2,
    "In Progress": 3,
    "Applied": 4,
}

def _get_apify_client():
    """Create the API client only when a remote fetch is requested."""
    try:
        from apify_client import ApifyClient
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Fetching jobs requires the 'apify-client' package. "
            "Install it before running search_jobs()."
        ) from exc
    return ApifyClient(os.environ.get("APIFY_API_TOKEN", ""))


def _excel_value(value: object) -> str:
    """Convert API values to Excel-safe strings while keeping blank cells blank."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (list, dict)):
        return str(value) if value else ""
    return str(value)


def _normalise_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Return only the approved job-workbook columns, in their fixed order."""
    normalised = dataframe.reindex(columns=JOB_COLUMNS, fill_value="").copy()
    return normalised.map(_excel_value)


def _normalise_new_job(job: dict, fetched_at: str) -> dict[str, str]:
    """Filter a fetched API record to the workbook schema."""
    record = {column: _excel_value(job.get(column)) for column in JOB_COLUMNS}
    record["application_status"] = record["application_status"] or "Not Applied"
    record["fetched_at"] = record["fetched_at"] or fetched_at
    record["id"] = canonical_job_id(record)
    if not record["id"]:
        raise ValueError("Fetched job has neither a stable job ID nor a usable job URL.")
    for column in APPLICATION_METRIC_COLUMNS:
        record[column] = ""
    return record


def _migrate_existing_ids(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Normalize legacy IDs, retaining a traceable fallback for malformed rows."""
    migrated = dataframe.copy()
    for row_number, (index, row) in enumerate(migrated.iterrows(), start=2):
        record = row.to_dict()
        identity = canonical_job_id(record)
        if not identity:
            # These rows predate the identity policy and contain no source ID or
            # usable URL. They cannot be safely deduplicated with new jobs.
            payload = "\x1f".join(str(record.get(column, "")) for column in JOB_COLUMNS)
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
            identity = f"legacy:{digest}:row-{row_number}"
        migrated.at[index, "id"] = identity
    return migrated


def _consolidate_duplicate_jobs(dataframe: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Keep one row per deterministic ID without discarding useful values.

    A stronger application outcome wins. Blank descriptive fields are filled
    from the other duplicate rows, but their application-specific fields are
    retained only from the winning record so statuses cannot be mixed.
    """
    merged_records = []
    duplicates_removed = 0
    for _, group in dataframe.groupby("id", sort=False, dropna=False):
        records = group.fillna("").to_dict(orient="records")
        if len(records) == 1:
            merged_records.append(records[0])
            continue

        def ranking(record: dict[str, str]) -> tuple[int, pd.Timestamp]:
            status_rank = _APPLICATION_STATUS_PRIORITY.get(record["application_status"], -1)
            timestamp = pd.to_datetime(record.get("fetched_at"), errors="coerce", utc=True)
            return status_rank, timestamp if not pd.isna(timestamp) else pd.Timestamp(0, tz="UTC")

        winner = max(records, key=ranking).copy()
        application_fields = {
            "application_status", "status", "reason", *APPLICATION_METRIC_COLUMNS,
        }
        for record in records:
            for column in JOB_COLUMNS:
                if column in application_fields or winner.get(column):
                    continue
                winner[column] = record.get(column, "")
        merged_records.append(winner)
        duplicates_removed += len(records) - 1

    return _normalise_columns(pd.DataFrame(merged_records)), duplicates_removed


def save_jobs_to_excel(jobs: list[dict], file_path: Path = EXCEL_PATH):
    """
    Appends new job search results to an Excel file.

    - New jobs receive ``application_status = "Not Applied"`` and a
      ``fetched_at`` timestamp.
    - Existing rows are **never overwritten**, so manually-updated status
      values are preserved across runs.
    - Deduplication uses a canonical URL/LinkedIn posting identity, so a
      tracking-URL variant or a different ATS requisition ID cannot create a
      second queue entry for the same posting.
    """
    # Ensure data directory exists
    file_path.parent.mkdir(parents=True, exist_ok=True)

    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    processed_jobs = [_normalise_new_job(job, now) for job in jobs]

    unique_jobs = []
    seen_identities = set()
    for record in processed_jobs:
        identity = job_identity(record)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        unique_jobs.append(record)
    new_df = _normalise_columns(pd.DataFrame(unique_jobs))

    # Read existing rows, then always project them onto the approved schema.
    # This removes obsolete columns from the workbook on every fetch.
    existing_df = pd.DataFrame(columns=JOB_COLUMNS)
    if file_path.exists() and file_path.stat().st_size > 0:
        try:
            existing_df = pd.read_excel(file_path, dtype=str)
        except Exception as exc:
            print(f"⚠️  Could not read existing {file_path}, starting fresh: {exc}")
            existing_df = pd.DataFrame()
    existing_df = _migrate_existing_ids(_normalise_columns(existing_df))

    existing_keys = {
        job_identity(record)
        for record in existing_df.fillna("").to_dict(orient="records")
    }
    existing_fingerprints = {
        candidate_job_fingerprint(record)
        for record in existing_df.fillna("").to_dict(orient="records")
    }
    unique_new_rows = []
    for record in new_df.fillna("").to_dict(orient="records"):
        identity = job_identity(record)
        if identity in existing_keys:
            continue
        fingerprint = candidate_job_fingerprint(record)
        if fingerprint and fingerprint in existing_fingerprints:
            print(
                "⚠️  Possible duplicate detected by company/title/location; "
                f"keeping job {record['id']} because no strong identifier matched."
            )
        existing_keys.add(identity)
        if fingerprint:
            existing_fingerprints.add(fingerprint)
        unique_new_rows.append(record)

    combined_df = pd.concat(
        [existing_df, _normalise_columns(pd.DataFrame(unique_new_rows))],
        ignore_index=True,
    )
    combined_df, duplicates_removed = _consolidate_duplicate_jobs(combined_df)
    combined_df.to_excel(file_path, index=False)

    if duplicates_removed:
        print(f"✅ Consolidated {duplicates_removed} duplicate job row(s) by strong ID.")

    if file_path.exists() and len(existing_df):
        print(f"✅ Kept {len(existing_df)} existing job(s) and appended "
              f"{len(unique_new_rows)} new job(s) to {file_path} "
              f"(total: {len(combined_df)})")
    else:
        print(f"✅ Created {file_path} with {len(combined_df)} job(s)")


def search_jobs(keywords, location, under10Applicants: bool = False ):
    """
    Search LinkedIn job postings using Apify.

    Use this tool when the user asks to find current or recently
    posted jobs. Returns structured job listings and saves them to data/applied_jobs.xlsx.
    """

    client = _get_apify_client()
    run_input = {
        "keywords": keywords,   # string
        "location": location, # string
        "geoId": "",  # string
        "distance": 0,  # int
        "datePosted": "past24Hours",  #"anyTime", "past24Hours", "pastWeek", "pastMonth" 
        "companyIds": [],  # list of strings
        "under10Applicants": under10Applicants,  # boolean
        "autoConvertToAiSearch": True,  # boolean
        "scrapeCompany": True, # boolean
        "limitPerSource": 50, # int
        "splitByLocation": False, # boolean
        "splitCountry": "US", # string
    }

    run = client.actor("hKByXkMQaC5Qt9UMN").call(
        run_input=run_input
    )

    dataset_id = run["default_dataset_id"] if isinstance(run, dict) else run.default_dataset_id

    results = list(client.dataset(dataset_id).iterate_items())
    
    save_jobs_to_excel(results, EXCEL_PATH)

    return results


if __name__ == "__main__":
    search_jobs(keywords="AI Engineer Entry Level", location="United States")
