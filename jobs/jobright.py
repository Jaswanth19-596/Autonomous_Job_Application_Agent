"""Normalize Jobright recommendation results for the applications workbook."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

from openpyxl import load_workbook

from jobs.fetcher import APPLICATION_METRIC_COLUMNS, JOB_COLUMNS
from workbook.job_identity import canonical_job_id


JOBRIGHT_RECOMMENDED_URL = "https://jobright.com/jobs/recommended"


def recent_workbook_job_summaries(
    path: Path,
    *,
    limit: int = 20,
) -> list[dict[str, str]]:
    """Return the most recent company/role pairs from the workbook.

    This intentionally includes every status, including ``Not Applied``. A job
    already present in the workbook has already been seen and must not be
    fetched from Jobright again. ``fetched_at`` is preferred when it exists;
    older rows without a timestamp retain worksheet order, with the newest row
    treated as the most recent record.
    """
    if limit < 1 or not path.is_file():
        return []

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["Applications"] if "Applications" in workbook.sheetnames else workbook.worksheets[0]
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        headers = {
            str(value).strip(): index
            for index, value in enumerate(header_row)
            if value not in (None, "")
        }
        required_headers = {"companyName", "title"}
        if not required_headers.issubset(headers):
            return []

        timestamp_headers = [
            header
            for header in (
                "fetched_at",
                "application_finished_at",
                "application_started_at",
            )
            if header in headers
        ]
        jobs: list[tuple[str, int, dict[str, str]]] = []
        for row_number, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            def value_for(header: str) -> str:
                index = headers[header]
                return "" if index >= len(row) or row[index] is None else str(row[index]).strip()

            company = value_for("companyName")
            title = value_for("title")
            if not company or not title:
                continue
            timestamp = next(
                (value_for(header) for header in timestamp_headers if value_for(header)),
                "",
            )
            jobs.append((timestamp, row_number, {"companyName": company, "title": title}))

        jobs.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [summary for _, _, summary in jobs[:limit]]
    finally:
        workbook.close()


def format_recent_workbook_jobs_for_prompt(
    jobs: Iterable[Mapping[str, str]],
) -> str:
    """Render trusted workbook data as a compact, clearly delimited prompt list."""
    lines = [
        f"- Company: {application['companyName']}; Role: {application['title']}"
        for application in jobs
    ]
    return "\n".join(lines) if lines else "- No prior jobs are recorded in the workbook."


def _is_jobright_url(url: str) -> bool:
    """Return whether a URL still points to Jobright instead of the employer/ATS."""
    hostname = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return (
        hostname in {"jobright.com", "jobright.ai"}
        or hostname.endswith(".jobright.com")
        or hostname.endswith(".jobright.ai")
    )


def workbook_rows_from_jobright_jobs(
    jobs: Iterable[Mapping[str, Any] | Any],
    *,
    expected_count: int = 5,
) -> list[dict[str, str]]:
    """Return the requested Jobright listings projected onto the workbook schema.

    The input sequence is deliberately not sorted: Jobright has already ranked
    these recommendations by match, and that on-page order is the source of
    truth for the spreadsheet queue.
    """
    if not 1 <= expected_count <= 5:
        raise ValueError("Jobright job count must be between 1 and 5.")

    jobs = list(jobs)
    if len(jobs) != expected_count:
        raise ValueError(
            f"Expected the top {expected_count} Jobright jobs, received {len(jobs)}."
        )

    rows: list[dict[str, str]] = []
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for expected_rank, job in enumerate(jobs, start=1):
        record = job.model_dump() if hasattr(job, "model_dump") else dict(job)
        rank = int(record.get("rank", 0))
        if rank != expected_rank:
            raise ValueError(
                "Jobright results must contain ranks 1 through "
                f"{expected_count} in their displayed order."
            )

        link = str(record.get("link") or "").strip()
        if link.startswith("/"):
            link = urljoin(JOBRIGHT_RECOMMENDED_URL, link)
        if not link:
            raise ValueError(f"Jobright result #{expected_rank} has no listing URL.")

        apply_url = str(record.get("applyUrl") or "").strip()
        if apply_url.startswith("/"):
            apply_url = urljoin(JOBRIGHT_RECOMMENDED_URL, apply_url)
        if not apply_url:
            raise ValueError(
                f"Jobright result #{expected_rank} has no resolved external application URL."
            )
        if _is_jobright_url(apply_url):
            raise ValueError(
                f"Jobright result #{expected_rank} still points to Jobright instead of "
                "the external application URL."
            )

        row = {column: "" for column in JOB_COLUMNS}
        for column in (
            "title",
            "companyName",
            "location",
            "postedAt",
            "salary",
            "descriptionText",
            "seniorityLevel",
            "employmentType",
        ):
            row[column] = str(record.get(column) or "").strip()
        row.update(
            {
                # The rest of the workflow follows ``link``. Store the final
                # employer/ATS destination rather than the Jobright detail page.
                "link": apply_url,
                "applyUrl": apply_url,
                "jobboard": "Jobright",
                "application_status": "Not Applied",
                "fetched_at": fetched_at,
            }
        )
        row["id"] = canonical_job_id(row)
        if not row["id"]:
            raise ValueError(f"Jobright result #{expected_rank} has no stable identity.")
        for column in APPLICATION_METRIC_COLUMNS:
            row[column] = ""
        rows.append(row)

    return rows
