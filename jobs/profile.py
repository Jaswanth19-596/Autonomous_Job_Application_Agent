"""Fetch and normalize one job posting supplied as a URL."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urlparse

from src.data.job_identity import linkedin_job_id


_MAX_EXCEL_TEXT_LENGTH = 32_767


class _JobPageParser(HTMLParser):
    """Collect the small subset of page data useful for a job record."""

    def __init__(self) -> None:
        super().__init__()
        self.metadata: dict[str, str] = {}
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "").strip()
            if key and content and key not in self.metadata:
                self.metadata[key] = content
        elif tag == "script" and attributes.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_ld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_ld_parts))
            self._in_json_ld = False
            self._json_ld_parts = []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _excel_text(value: Any) -> str:
    """Return a string that is safe to persist in an Excel cell."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))
    return text[:_MAX_EXCEL_TEXT_LENGTH]


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _job_posting_from_json_ld(raw_documents: list[str]) -> dict[str, Any]:
    for raw_document in raw_documents:
        try:
            document = json.loads(raw_document.strip())
        except (TypeError, json.JSONDecodeError):
            continue
        for item in _walk_json(document):
            raw_type = item.get("@type", "")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            if any(str(value).lower() == "jobposting" for value in types):
                return item
    return {}


def _location(job_posting: dict[str, Any]) -> str:
    locations = job_posting.get("jobLocation", [])
    if not isinstance(locations, list):
        locations = [locations]
    for location in locations:
        address = location.get("address", {}) if isinstance(location, dict) else {}
        if isinstance(address, str):
            return _clean_text(address)
        if isinstance(address, dict):
            parts = [
                address.get("streetAddress"),
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("postalCode"),
                address.get("addressCountry"),
            ]
            result = ", ".join(_clean_text(part) for part in parts if _clean_text(part))
            if result:
                return result
    return ""


def _salary(job_posting: dict[str, Any]) -> str:
    value = job_posting.get("baseSalary", "")
    if isinstance(value, dict):
        currency = _clean_text(value.get("currency"))
        amount = value.get("value", value.get("amount", ""))
        if isinstance(amount, dict):
            low, high, unit = amount.get("minValue"), amount.get("maxValue"), amount.get("unitText")
            range_value = " - ".join(str(item) for item in (low, high) if item not in (None, ""))
            return " ".join(item for item in (currency, range_value, _clean_text(unit)) if item)
        return " ".join(item for item in (currency, _clean_text(amount)) if item)
    return _clean_text(value)


def _identifier(job_posting: dict[str, Any], url: str) -> str:
    # A LinkedIn URL contains the stable posting ID. JSON-LD identifiers often
    # expose an employer requisition, or even an organization name, which can
    # differ from the queue's LinkedIn-sourced ID and create a duplicate row.
    linked_in_id = linkedin_job_id(url)
    if linked_in_id:
        return linked_in_id

    value = job_posting.get("identifier", "")
    if isinstance(value, dict):
        value = value.get("value") or value.get("name") or ""
    if value not in (None, ""):
        return str(value).strip()

    return f"url-{sha256(url.encode('utf-8')).hexdigest()[:16]}"


def _title_and_company_from_metadata(metadata: dict[str, str], page_title: str) -> tuple[str, str]:
    title = _clean_text(metadata.get("job:title") or metadata.get("twitter:title") or metadata.get("og:title"))
    company = _clean_text(
        metadata.get("job:company")
        or metadata.get("company")
        or metadata.get("twitter:site")
    )
    source = title or _clean_text(page_title)
    match = re.match(r"(.+?)\s+(?:at|[-|])\s+(.+?)(?:\s+[|•-]\s+.*)?$", source, re.IGNORECASE)
    if match:
        title = title or _clean_text(match.group(1))
        company = company or _clean_text(match.group(2))
    return title, company


def extract_job_profile(
    *,
    url: str,
    html: str,
    page_title: str = "",
    body_text: str = "",
) -> dict[str, str]:
    """Turn a retrieved job page into the spreadsheet's normalized job record."""
    parser = _JobPageParser()
    parser.feed(html)
    job_posting = _job_posting_from_json_ld(parser.json_ld)
    metadata = parser.metadata

    metadata_title, metadata_company = _title_and_company_from_metadata(metadata, page_title)
    organization = job_posting.get("hiringOrganization", {})
    if isinstance(organization, dict):
        organization = organization.get("name", "")
    description_html = _excel_text(job_posting.get("description") or "")
    description_text = _clean_text(description_html) or _clean_text(body_text)
    title = _clean_text(job_posting.get("title")) or metadata_title or "Unknown position"
    company = _clean_text(organization) or metadata_company or "Unknown company"
    hostname = (urlparse(url).hostname or "").lower()

    return {
        "id": _identifier(job_posting, url),
        "link": url,
        "applyUrl": url,
        "title": _excel_text(title),
        "companyName": _excel_text(company),
        "location": _excel_text(_location(job_posting)),
        "postedAt": _excel_text(job_posting.get("datePosted")),
        "employmentType": _excel_text(job_posting.get("employmentType")),
        "salary": _excel_text(_salary(job_posting)),
        "descriptionHtml": description_html,
        "descriptionText": _excel_text(description_text),
        "jobboard": hostname,
        "application_status": "Not Applied",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def _load_page(url: str, timeout_ms: int) -> tuple[str, str, str, str]:
    """Load a page in a short-lived browser and return its useful contents."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return page.url, page.title(), page.content(), page.locator("body").inner_text()
        finally:
            browser.close()


def fetch_job_profile(url: str, timeout_ms: int = 30_000) -> dict[str, str]:
    """Fetch an http(s) job URL and return an insert-ready profile dictionary."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Job URL must be a complete http(s) URL.")
    final_url, page_title, html, body_text = _load_page(url.strip(), timeout_ms)
    return extract_job_profile(
        url=final_url,
        html=html,
        page_title=page_title,
        body_text=body_text,
    )
