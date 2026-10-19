"""Local MCP tools backed by the stable Gmail REST API v1.

Google's public Gmail API is a REST API; it is not available as a remote MCP
transport. This adapter keeps Gmail access inside the application's existing
MCP workflow while making only documented ``gmail.googleapis.com/gmail/v1``
requests.
"""

from __future__ import annotations

import asyncio
import base64
import json
from html import unescape
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mcp.server.fastmcp import FastMCP

from mcp_connection.mcp_manager import get_gmail_credentials
import os
from dotenv import load_dotenv
load_dotenv()

GMAIL_API_BASE_URL = os.environ["GMAIL_API_BASE_URL"]
DEFAULT_MAX_RESULTS = int(os.environ["DEFAULT_MAX_RESULTS"])
MAX_SEARCH_RESULTS = int(os.environ["MAX_SEARCH_RESULTS"])
DEFAULT_MAX_BODY_CHARACTERS = int(os.environ["DEFAULT_MAX_BODY_CHARACTERS"])
MAX_BODY_CHARACTERS = int(os.environ["MAX_BODY_CHARACTERS"])

mcp = FastMCP(
    "gmail-standard-api",
    instructions=(
        "Search and read Gmail messages through the stable Gmail REST API v1. "
        "Use search_messages first, then get_message only for relevant results."
    ),
)


def _gmail_api_url(path: str, **query: object) -> str:
    """Build a documented Gmail REST v1 URL with no preview endpoint."""
    encoded_query = urlencode(
        {key: value for key, value in query.items() if value is not None}
    )
    url = f"{GMAIL_API_BASE_URL}/{path.lstrip('/')}"
    return f"{url}?{encoded_query}" if encoded_query else url


def _gmail_request(path: str, **query: object) -> dict[str, Any]:
    """Call Gmail REST v1 using the cached OAuth credential."""
    credentials = get_gmail_credentials()
    request = Request(
        _gmail_api_url(path, **query),
        headers={"Authorization": f"Bearer {credentials.token}"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Gmail REST API request failed ({exc.code}): {detail}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the Gmail REST API: {exc.reason}") from exc


def _header_value(message: dict[str, Any], name: str) -> str:
    headers = message.get("payload", {}).get("headers", [])
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _decode_body(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _plain_text_parts(payload: dict[str, Any]) -> list[str]:
    """Extract only inline, readable message text; omit attachment contents."""
    parts: list[str] = []
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime_type == "text/plain" and body.get("data"):
        parts.append(_decode_body(body["data"]))

    for part in payload.get("parts", []):
        parts.extend(_plain_text_parts(part))

    # Some simple messages omit mimeType but still carry their body directly.
    if not parts and body.get("data") and not payload.get("parts"):
        parts.append(_decode_body(body["data"]))
    return parts


def _message_summary(message: dict[str, Any]) -> dict[str, str]:
    return {
        "id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "from": _header_value(message, "From"),
        "subject": _header_value(message, "Subject"),
        "date": _header_value(message, "Date"),
        "snippet": message.get("snippet", ""),
    }


@mcp.tool()
async def search_messages(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> dict[str, Any]:
    """Search recent Gmail messages with Gmail's documented search syntax.

    Examples: ``from:company.com newer_than:30d`` or
    ``"application received" "Acme" newer_than:90d``. Results include only
    identifying metadata and snippets. Read a likely result with get_message.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= max_results <= MAX_SEARCH_RESULTS:
        raise ValueError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")

    listing = await asyncio.to_thread(
        _gmail_request,
        "users/me/messages",
        q=query,
        maxResults=max_results,
    )
    message_ids = [item["id"] for item in listing.get("messages", [])]
    messages = await asyncio.gather(
        *(
            asyncio.to_thread(_gmail_request, f"users/me/messages/{message_id}", format="metadata")
            for message_id in message_ids
        )
    )
    return {
        "result_size_estimate": listing.get("resultSizeEstimate", 0),
        "messages": [_message_summary(message) for message in messages],
    }


@mcp.tool()
async def get_message(
    message_id: str,
    max_body_characters: int = DEFAULT_MAX_BODY_CHARACTERS,
) -> dict[str, str]:
    """Read headers and plain-text body from one Gmail message by its ID.

    Use this only after search_messages identifies a relevant result. Attachments
    are not downloaded or exposed.
    """
    if not message_id.strip():
        raise ValueError("message_id must not be empty")
    if not 1 <= max_body_characters <= MAX_BODY_CHARACTERS:
        raise ValueError(
            f"max_body_characters must be between 1 and {MAX_BODY_CHARACTERS}"
        )

    message = await asyncio.to_thread(
        _gmail_request, f"users/me/messages/{message_id}", format="full"
    )
    text = "\n".join(_plain_text_parts(message.get("payload", {}))).strip()
    if not text:
        # Gmail snippets are safe, small fallbacks when an email has HTML only.
        text = unescape(re.sub(r"<[^>]+>", " ", message.get("snippet", "")))
    if len(text) > max_body_characters:
        text = f"{text[:max_body_characters]}\n[Body truncated]"

    return {
        **_message_summary(message),
        "body": text,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
