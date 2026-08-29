"""Stable, source-qualified job identities shared by all ingestion paths."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_QUERY_KEYS = {
    "refid", "trackingid", "trk", "src", "source", "position", "pagenum",
}
_JOB_ID_QUERY_KEYS = {"jobid", "job_id", "requisitionid", "requisition_id", "gh_jid"}


def linkedin_job_id(url: object) -> str:
    """Return the stable numeric posting ID from a LinkedIn job URL."""
    value = str(url or "").strip()
    parsed = urlsplit(value)
    if parsed.hostname not in {"linkedin.com", "www.linkedin.com"}:
        return ""
    match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)", parsed.path, re.IGNORECASE)
    return match.group(1) if match else ""


def canonical_job_url(url: object) -> str:
    """Normalize a job URL while dropping non-identifying tracking parameters."""
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


def _hostname(url: object) -> str:
    hostname = (urlsplit(str(url or "")).hostname or "").lower()
    return hostname.removeprefix("www.")


def source_job_id_from_url(url: object) -> str:
    """Return a source-qualified external job ID when a URL exposes one."""
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    hostname = _hostname(value)
    path_parts = [part for part in parsed.path.split("/") if part]

    linked_in_id = linkedin_job_id(value)
    if linked_in_id:
        return f"linkedin:{linked_in_id}"

    # boards.greenhouse.io/<company>/jobs/<id>
    if hostname.endswith("greenhouse.io") and len(path_parts) >= 3 and path_parts[-2] == "jobs":
        return f"greenhouse:{path_parts[-3].lower()}:{path_parts[-1]}"

    # jobs.lever.co/<company>/<posting-id>
    if hostname.endswith("lever.co") and len(path_parts) >= 2:
        return f"lever:{path_parts[-2].lower()}:{path_parts[-1]}"

    query = {key.lower(): item for key, item in parse_qsl(parsed.query, keep_blank_values=True)}
    for key in _JOB_ID_QUERY_KEYS:
        if query.get(key):
            return f"{hostname}:{query[key]}"

    return ""


def _fallback_url_identity(url: object) -> str:
    canonical_url = canonical_job_url(url)
    if not canonical_url:
        return ""
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:20]
    return f"url:{digest}"


def job_identity(record: dict[str, object]) -> str:
    """Build a deterministic workbook ID, prioritizing application identity."""
    for field in ("applyUrl", "link"):
        external_id = source_job_id_from_url(record.get(field))
        if external_id:
            return external_id

    stored_id = str(record.get("id") or "").strip()
    if stored_id:
        if stored_id.startswith("source:legacy:"):
            return stored_id.removeprefix("source:")
        if ":" in stored_id:
            return stored_id
        source = str(record.get("jobboard") or "source").strip().lower()
        source = re.sub(r"[^a-z0-9]+", "-", source).strip("-") or "source"
        return f"{source}:{stored_id}"

    for field in ("applyUrl", "link"):
        fallback = _fallback_url_identity(record.get(field))
        if fallback:
            return fallback
    return ""


def canonical_job_id(record: dict[str, object]) -> str:
    """Return the deterministic ID stored in the workbook."""
    return job_identity(record)


def candidate_job_fingerprint(record: dict[str, object]) -> str:
    """Create a non-authoritative company/title/location duplicate signal."""
    def normalize(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().casefold())

    company = normalize(record.get("companyName"))
    title = normalize(record.get("title"))
    location = normalize(record.get("location"))
    employment_type = normalize(record.get("employmentType"))
    if not company or not title:
        return ""
    payload = "\x1f".join((company, title, location, employment_type))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def job_identity_aliases(record: dict[str, object]) -> set[str]:
    """Return all strong identifiers available for matching an existing row."""
    aliases = {str(record.get("id") or "").strip(), job_identity(record)}
    for field in ("applyUrl", "link"):
        value = record.get(field)
        aliases.add(source_job_id_from_url(value))
        aliases.add(linkedin_job_id(value))  # Raw ID lookup supports legacy rows.
    return {alias for alias in aliases if alias}
