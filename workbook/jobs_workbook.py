"""Targeted applied_jobs.xlsx mutations that preserve dashboard sheets and formatting."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from workbook.job_identity import canonical_job_url, job_identity, job_identity_aliases


@dataclass(frozen=True)
class JobRowsUpsertResult:
    """Summary of a batch write that never creates duplicate workbook rows."""

    inserted: int = 0
    updated: int = 0
    skipped_duplicates: int = 0


def _applications_sheet(workbook):
    return workbook["Applications"] if "Applications" in workbook.sheetnames else workbook.worksheets[0]


def _headers(sheet) -> dict[str, int]:
    return {
        str(cell.value): cell.column
        for cell in sheet[1]
        if cell.value not in (None, "")
    }


def _matching_rows_for_job_id(sheet, headers: dict[str, int], job_id: str) -> list[int]:
    """Match a deterministic ID or any strong identity available in the row."""
    target = str(job_id).strip()
    matches = []
    for row in range(2, sheet.max_row + 1):
        record = {
            field: sheet.cell(row, headers[field]).value
            for field in ("id", "link", "applyUrl", "jobboard")
            if field in headers
        }
        if target in job_identity_aliases(record):
            matches.append(row)
    return matches


def get_job_row(path: Path, job_id: str) -> dict[str, str] | None:
    """Return one Applications row by ID without loading the entire sheet.

    The workbook is opened in read-only mode and rows are streamed until the
    requested ID is found.  This is intentionally separate from the pandas
    queue reader, which materializes every job before filtering and sorting.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = _applications_sheet(workbook)
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        headers = {
            str(value).strip(): index
            for index, value in enumerate(header_row)
            if value not in (None, "")
        }
        if "id" not in headers:
            raise ValueError(f"'id' column missing in {path}")

        target_id = str(job_id).strip()
        matches = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            record = {
                field: row[headers[field]]
                for field in ("id", "link", "applyUrl", "jobboard")
                if field in headers and headers[field] < len(row)
            }
            if target_id not in job_identity_aliases(record):
                continue
            matches.append({
                header: "" if index >= len(row) or row[index] is None else str(row[index])
                for header, index in headers.items()
            })
        if len(matches) > 1:
            raise ValueError(
                f"Job ID '{job_id}' is ambiguous: {len(matches)} rows share it. "
                "Use a unique canonical job ID before updating or delegating."
            )
        return matches[0] if matches else None
    finally:
        workbook.close()


def _extend_tables(sheet) -> None:
    if not sheet.tables:
        return
    reference = f"A1:{get_column_letter(sheet.max_column)}{sheet.max_row}"
    for table in sheet.tables.values():
        table.ref = reference


def find_job_rows_by_canonical_url(path: Path, url: str) -> list[dict[str, str]]:
    """Return every row matching a normalized URL without mutating the workbook."""
    target = canonical_job_url(url)
    if not target:
        return []
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = _applications_sheet(workbook)
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
        headers = {
            str(value).strip(): index
            for index, value in enumerate(header_row)
            if value not in (None, "")
        }
        result = []
        for row in sheet.iter_rows(min_row=2, values_only=True):
            record = {
                header: "" if index >= len(row) or row[index] is None else str(row[index])
                for header, index in headers.items()
            }
            if any(canonical_job_url(record.get(field)) == target for field in ("link", "applyUrl")):
                result.append(record)
        return result
    finally:
        workbook.close()


def update_job_row(path: Path, job_id: str, updates: dict[str, Any]) -> None:
    """Update one job by ID without replacing other workbook sheets."""
    workbook = load_workbook(path)
    sheet = _applications_sheet(workbook)
    headers = _headers(sheet)
    if "id" not in headers:
        raise ValueError(f"'id' column missing in {path}")

    target_rows = _matching_rows_for_job_id(sheet, headers, job_id)
    if not target_rows:
        raise ValueError(f"Job with ID '{job_id}' not found in {path}")
    if len(target_rows) > 1:
        raise ValueError(
            f"Job ID '{job_id}' is ambiguous: {len(target_rows)} rows share it. "
            "Refusing to update an arbitrary row."
        )
    target_row = target_rows[0]

    for header, value in updates.items():
        column = headers.get(header)
        if column is None:
            column = sheet.max_column + 1
            sheet.cell(1, column, header)
            headers[header] = column
        sheet.cell(target_row, column, value)

    _extend_tables(sheet)
    workbook.save(path)


def upsert_job_row(
    path: Path,
    record: dict[str, Any],
    *,
    preserve_existing: set[str] | None = None,
) -> tuple[str, int]:
    """Add a job record or refresh its details without replacing the workbook.

    URL fields are checked before the identifier. This allows a URL supplied by
    a user to refresh a pre-existing row even when the job board changed the
    identifier exposed in its page markup.
    """
    if not str(record.get("id", "")).strip():
        raise ValueError("A job record needs a non-empty 'id'.")

    workbook = load_workbook(path)
    sheet = _applications_sheet(workbook)
    headers = _headers(sheet)
    for header in record:
        if header not in headers:
            column = sheet.max_column + 1
            sheet.cell(1, column, header)
            headers[header] = column

    canonical_urls = {
        canonical_job_url(record.get(field))
        for field in ("link", "applyUrl")
        if canonical_job_url(record.get(field))
    }
    canonical_matches = []
    if canonical_urls:
        for row in range(2, sheet.max_row + 1):
            row_urls = {
                canonical_job_url(sheet.cell(row, headers[field]).value)
                for field in ("link", "applyUrl")
                if field in headers and canonical_job_url(sheet.cell(row, headers[field]).value)
            }
            if canonical_urls.intersection(row_urls):
                canonical_matches.append(row)
    if len(canonical_matches) > 1:
        raise ValueError(
            "Canonical job URL matches multiple rows. Resolve the existing duplicate "
            "records before refreshing this job."
        )
    target_row = canonical_matches[0] if canonical_matches else None

    if target_row is None:
        incoming_aliases = job_identity_aliases(record)
        id_matches = [
            row for row in range(2, sheet.max_row + 1)
            if incoming_aliases.intersection(job_identity_aliases({
                field: sheet.cell(row, headers[field]).value
                for field in ("id", "link", "applyUrl", "jobboard")
                if field in headers
            }))
        ]
        if len(id_matches) > 1:
            raise ValueError(
                f"Job identity '{record['id']}' is ambiguous: {len(id_matches)} rows share it. "
                "Refusing to refresh an arbitrary row."
            )
        target_row = id_matches[0] if id_matches else None

    action = "updated" if target_row is not None else "inserted"
    if target_row is None:
        target_row = sheet.max_row + 1
        style_source_row = max(2, target_row - 1)
        for column in range(1, sheet.max_column + 1):
            source = sheet.cell(style_source_row, column)
            target = sheet.cell(target_row, column)
            if source.has_style:
                target._style = copy(source._style)
            target.number_format = source.number_format

    protected_headers = preserve_existing or set()
    for header, value in record.items():
        if action == "updated" and header in protected_headers:
            continue
        sheet.cell(target_row, headers[header], value)

    _extend_tables(sheet)
    workbook.save(path)
    return action, target_row


def upsert_job_rows(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    preserve_existing: set[str] | None = None,
) -> JobRowsUpsertResult:
    """Persist a batch of job rows without adding duplicate jobs.

    Each row is compared with the existing workbook through ``upsert_job_row``.
    Duplicate records returned in the same batch are skipped before they can
    overwrite one another. Existing jobs are refreshed in place, preserving the
    caller-specified fields such as application status.
    """
    inserted = updated = skipped_duplicates = 0
    batch_aliases: set[str] = set()

    for row in rows:
        aliases = job_identity_aliases(row)
        if aliases.intersection(batch_aliases):
            skipped_duplicates += 1
            continue

        action, _ = upsert_job_row(
            path,
            row,
            preserve_existing=preserve_existing,
        )
        batch_aliases.update(aliases)
        if action == "inserted":
            inserted += 1
        else:
            updated += 1

    return JobRowsUpsertResult(
        inserted=inserted,
        updated=updated,
        skipped_duplicates=skipped_duplicates,
    )


def append_job_rows(path: Path, rows: list[dict[str, Any]]) -> int:
    """Append jobs not already present, preserving dashboard and table styling."""
    if not rows:
        return 0
    workbook = load_workbook(path)
    sheet = _applications_sheet(workbook)
    headers = _headers(sheet)
    for row in rows:
        for header in row:
            if header not in headers:
                column = sheet.max_column + 1
                sheet.cell(1, column, header)
                headers[header] = column

    existing_ids = {
        str(sheet.cell(row, headers["id"]).value).strip()
        for row in range(2, sheet.max_row + 1)
    } if "id" in headers else set()
    existing_identities = set()
    for row in range(2, sheet.max_row + 1):
        existing_identities.add(job_identity({
            field: sheet.cell(row, headers[field]).value
            for field in ("id", "link", "applyUrl") if field in headers
        }))

    appended = 0
    for record in rows:
        record_id = str(record.get("id", "")).strip()
        if record_id in existing_ids or job_identity(record) in existing_identities:
            continue
        target_row = sheet.max_row + 1
        style_source_row = max(2, target_row - 1)
        for column in range(1, sheet.max_column + 1):
            source = sheet.cell(style_source_row, column)
            target = sheet.cell(target_row, column)
            if source.has_style:
                target._style = copy(source._style)
            target.number_format = source.number_format
        for header, value in record.items():
            sheet.cell(target_row, headers[header], value)
        existing_ids.add(record_id)
        existing_identities.add(job_identity(record))
        appended += 1

    _extend_tables(sheet)
    workbook.save(path)
    return appended
