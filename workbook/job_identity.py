"""Stable job identities shared by ingestion and workbook operations."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_QUERY_KEYS = {
    "refid",
    "trackingid",
    "trk",
    "src",
    "source",
    "position",
    "pagenum",
}


def linkedin_job_id(url: object) -> str:
    """Return the stable numeric posting ID from a LinkedIn job URL."""
    value = str(url or "").strip()
    parsed = urlsplit(value)
    if parsed.hostname not in {"linkedin.com", "www.linkedin.com"}:
        return ""
    match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)", parsed.path, re.IGNORECASE)
    return match.group(1) if match else ""


def canonical_job_url(url: object) -> str:
    """Normalize a job URL while retaining parameters that identify the job.

    LinkedIn's numeric posting ID is part of its path, so all query parameters
    are tracking-only there. Other boards can use query parameters as identity,
    therefore only known tracking parameters are removed for those URLs.
    """
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value.rstrip("/")

    hostname = parsed.hostname.lower() if parsed.hostname else ""
    path = parsed.path.rstrip("/") or "/"
    if hostname in {"linkedin.com", "www.linkedin.com"} and linkedin_job_id(value):
        query = ""
    else:
        kept = [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")
        ]
        query = urlencode(sorted(kept))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def job_identity(record: dict[str, object]) -> str:
    """Return a stable deduplication key for a queued job record."""
    for field in ("link", "applyUrl"):
        value = str(record.get(field) or "").strip()
        linked_in_id = linkedin_job_id(value)
        if linked_in_id:
            return f"linkedin:{linked_in_id}"
        canonical_url = canonical_job_url(value)
        if canonical_url:
            return f"url:{canonical_url}"
    return f"id:{str(record.get('id') or '').strip()}"


def canonical_job_id(record: dict[str, object]) -> str:
    """Prefer a LinkedIn posting ID over an unreliable stored source ID."""
    for field in ("link", "applyUrl"):
        linked_in_id = linkedin_job_id(record.get(field))
        if linked_in_id:
            return linked_in_id
    return str(record.get("id") or "").strip()
