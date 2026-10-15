from random import random
import os
import sys
import hashlib
from pathlib import Path
from typing import Any, Mapping
import pandas as pd
from dotenv import load_dotenv

# Ensure project root is in sys.path when running jobs/fetcher.py directly
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from workbook.job_identity import (
    candidate_job_fingerprint,
    canonical_job_id,
    canonical_job_url,
    job_identity,
)
from workbook.storage import with_workbook

load_dotenv()

# Determine project root and data directory
DATA_DIR = BASE_DIR / "data"
PENDING_EXCEL_PATH = DATA_DIR / "pending_jobs.xlsx"
EXCEL_PATH = PENDING_EXCEL_PATH

# Columns for pending_jobs.xlsx: only "url" and "fetched_at"
PENDING_JOB_COLUMNS = (
    "url",
    "fetched_at",
)

# Retained for backward compatibility across the agent (e.g., nodes.py)
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
    "application_elapsed_seconds",
    "application_cost_usd",
    "application_tool_calls",
    "llm_call_count",
    "total_tokens",
    "human_intervention_count",
    "applied_at",
)


def _get_apify_client():
    """Create the API client only when a remote fetch is requested."""
    try:
        from apify_client import ApifyClient
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Fetching jobs requires the 'apify-client' package. "
            "Install it before running search_jobs()."
        ) from exc
    token = os.environ.get("APIFY_API_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "Missing APIFY_API_TOKEN. Please set APIFY_API_TOKEN in your .env file or environment variables."
        )
    return ApifyClient(token)


def _extract_job_url(job: Mapping[str, Any] | Any) -> str:
    """Extract a usable job URL from an API job record, object, or string."""
    if isinstance(job, str):
        return job.strip()
    if hasattr(job, "model_dump"):
        job = job.model_dump()
    elif not isinstance(job, (dict, Mapping)):
        try:
            job = dict(job)
        except Exception:
            return ""

    for field in ("link", "applyUrl", "url", "jobUrl", "job_url"):
        value = str(job.get(field) or "").strip()
        if value and value.lower() not in {"none", "null", "nan"}:
            return value
    return ""


@with_workbook("file_path", write=True)
def save_jobs_to_excel(jobs: list[dict | str] | list[Any], file_path: Path = EXCEL_PATH) -> pd.DataFrame:
    """
    Appends new job search results to pending_jobs.xlsx with only the columns in pending_jobs.xlsx.

    - Deduplication uses canonical job URLs so that variants or tracking links are not duplicated.
    - Existing records in pending_jobs.xlsx are preserved.
    - The saved workbook contains only the columns: 'url' and 'fetched_at'.
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing rows from pending_jobs.xlsx if it exists
    existing_records = []
    seen_urls = set()

    if file_path.exists() and file_path.stat().st_size > 0:
        try:
            existing_df = pd.read_excel(file_path, dtype=str)
            for _, row in existing_df.iterrows():
                url = str(row.get("url") or row.get("link") or "").strip()
                if not url or url.lower() in {"none", "null", "nan"}:
                    continue
                canonical = canonical_job_url(url) or url
                if canonical not in seen_urls:
                    seen_urls.add(canonical)
                    fetched_at_val = row.get("fetched_at")
                    fetched_at_str = "" if (fetched_at_val is None or pd.isna(fetched_at_val)) else str(fetched_at_val).strip()
                    existing_records.append({
                        "url": url,
                        "fetched_at": fetched_at_str,
                    })
        except Exception as exc:
            raise ValueError(f"Could not read existing {file_path}; refusing to overwrite pending jobs.") from exc

    now = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    new_rows = []
    for job in jobs:
        url = _extract_job_url(job)
        if not url:
            continue
        canonical = canonical_job_url(url) or url
        if canonical in seen_urls:
            continue
        seen_urls.add(canonical)

        fetched_at = ""
        if isinstance(job, (dict, Mapping)):
            fetched_at = str(job.get("fetched_at") or "").strip()
        if not fetched_at or fetched_at.lower() in {"none", "null", "nan"}:
            fetched_at = now

        new_rows.append({
            "url": url,
            "fetched_at": fetched_at,
        })

    # Combine existing and new records, strictly maintaining PENDING_JOB_COLUMNS
    all_records = existing_records + new_rows
    combined_df = pd.DataFrame(all_records, columns=list(PENDING_JOB_COLUMNS)).fillna("")
    combined_df = combined_df.reindex(columns=list(PENDING_JOB_COLUMNS), fill_value="")

    combined_df.to_excel(file_path, index=False)

    if file_path.exists() and existing_records:
        print(
            f"✅ Kept {len(existing_records)} existing pending job(s) and appended "
            f"{len(new_rows)} new job(s) to {file_path} "
            f"(total: {len(combined_df)})"
        )
    else:
        print(f"✅ Created {file_path} with {len(combined_df)} pending job(s)")

    return combined_df


def search_jobs(keywords, location, under10Applicants: bool = False):
    """
    Search LinkedIn job postings using Apify.

    Use this tool when the user asks to find current or recently
    posted jobs. Returns structured job listings and saves them to data/pending_jobs.xlsx.
    """
    client = _get_apify_client()
    run_input = {
        "keywords": keywords,   # string
        "location": location,   # string
        "geoId": "",            # string
        "distance": 0,          # int
        "datePosted": "past24Hours",  # "anyTime", "past24Hours", "pastWeek", "pastMonth"
        "companyIds": [],       # list of strings
        "under10Applicants": under10Applicants,  # boolean
        "autoConvertToAiSearch": True,  # boolean
        "scrapeCompany": True,  # boolean
        "limitPerSource": 10,   # int
        "splitByLocation": False, # boolean
        "splitCountry": "US",   # string
    }

    run = client.actor("hKByXkMQaC5Qt9UMN").call(
        run_input=run_input
    )

    dataset_id = run["default_dataset_id"] if isinstance(run, dict) else run.default_dataset_id

    results = list(client.dataset(dataset_id).iterate_items())

    save_jobs_to_excel(results, EXCEL_PATH)

    return results


import random

if __name__ == "__main__":

    keywords = ["AI", "Machine Learning", "Data Science", "Software Engineering", "Robotics", "MLOps"]
    location = "United States"

    keywords = random.choice(keywords)
    search_jobs(keywords=keywords, location=location)
    search_jobs(keywords=keywords+" Entry Level", location=location)
    search_jobs(keywords=keywords+" New Grad", location=location)
