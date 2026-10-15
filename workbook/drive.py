"""Google Drive transport for the agent's Excel workbooks (no Sheets conversion)."""

from __future__ import annotations

import json
import hashlib
import logging
import random
import time
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from requests.exceptions import ConnectionError, Timeout

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
API_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


class DriveStorageError(RuntimeError):
    """A Drive operation failed; local workbooks must not be used as a fallback."""


class DriveTransientError(DriveStorageError):
    """A temporary transport or service failure that may be retried."""


MAX_ATTEMPTS = 4


def retry_delay(attempt):
    delay = 2 ** attempt + random.uniform(0, 1)
    logging.getLogger(__name__).warning(
        "Google Drive temporarily unavailable; retrying (%s/%s) in %.1fs",
        attempt + 1, MAX_ATTEMPTS, delay,
    )
    time.sleep(delay)


def config_path() -> Path:
    return Path(os.getenv("JOB_AGENT_DRIVE_CONFIG", str(PROJECT_ROOT / "user/drive_workbooks.json"))).expanduser()


def token_path() -> Path:
    return Path(os.getenv("JOB_AGENT_DRIVE_TOKEN_PATH", str(PROJECT_ROOT / "user/drive_token.json"))).expanduser()


def write_private_json(path: Path, data: dict) -> None:
    """Atomically save account configuration without exposing tokens in logs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def get_credentials(*, interactive: bool = False) -> Credentials:
    load_dotenv(PROJECT_ROOT / ".env")
    path = token_path()
    credentials = None
    if path.is_file():
        try:
            credentials = Credentials.from_authorized_user_file(str(path))
        except (OSError, ValueError):
            pass
    if credentials and credentials.has_scopes([DRIVE_SCOPE]):
        if credentials.valid:
            return credentials
        if credentials.refresh_token:
            try:
                credentials.refresh(Request())
                write_private_json(path, json.loads(credentials.to_json()))
                return credentials
            except RefreshError:
                pass
    if not interactive:
        raise DriveStorageError("Google Drive authorization is missing or expired. Run: uv run python -m workbook.drive_setup")
    client_id = os.getenv("GOOGLE_DRIVE_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise DriveStorageError("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env before connecting Drive.")
    flow = InstalledAppFlow.from_client_config({"installed": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }}, [DRIVE_SCOPE])
    credentials = flow.run_local_server(
        host="localhost", port=0, access_type="offline", prompt="consent",
        timeout_seconds=240,
        authorization_prompt_message="Approve Google Drive access in the browser to copy your job workbooks.",
        success_message="Google Drive connected. You can close this tab and return to the agent.",
    )
    if not credentials.has_scopes([DRIVE_SCOPE]):
        raise DriveStorageError("Google Drive file access was not granted; no workbooks were uploaded.")
    write_private_json(path, json.loads(credentials.to_json()))
    return credentials


class DriveClient:
    def __init__(self, credentials: Credentials):
        self.session = AuthorizedSession(credentials)

    def close(self):
        self.session.close()

    def request(self, method: str, url: str, **kwargs):
        # Writes need operation-specific reconciliation before they are retried.
        attempts = MAX_ATTEMPTS if method.upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                return self._request_once(method, url, **kwargs)
            except DriveTransientError:
                if attempt == attempts - 1:
                    raise
                retry_delay(attempt + 1)

    def _request_once(self, method: str, url: str, **kwargs):
        try:
            response = self.session.request(method, url, timeout=90, **kwargs)
        except (Timeout, ConnectionError) as exc:
            raise DriveTransientError("Google Drive did not respond; the operation could not be confirmed.") from exc
        except Exception as exc:
            raise DriveStorageError("Could not reach Google Drive; workbook access stopped.") from exc
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            response.close()
            raise DriveTransientError(f"Google Drive temporarily unavailable (HTTP {response.status_code}).")
        if response.status_code == 412:
            raise DriveStorageError("The Drive workbook changed during this update. Retry using its latest contents.")
        if not response.ok:
            message = f"Google Drive returned HTTP {response.status_code}."
            if response.status_code == 403:
                try:
                    payload = response.json()
                    details = payload.get("error", {}).get("details", []) if isinstance(payload, dict) else []
                except ValueError:
                    details = []
                if any(isinstance(detail, dict) and detail.get("reason") == "SERVICE_DISABLED" for detail in details):
                    message += " The Google Drive API is disabled for your OAuth project. Enable it in Google Cloud Console, then retry setup."
                else:
                    message += " Check Drive API is enabled for your Google OAuth project and file access is granted."
            raise DriveStorageError(message)
        return response

    def metadata(self, file_id: str):
        response = self.request("GET", f"{API_URL}/{file_id}", params={
            "fields": "id,name,mimeType,version,trashed,webViewLink,md5Checksum",
            "supportsAllDrives": "true",
        })
        return response.json(), response.headers.get("ETag")

    def download(self, file_id: str, destination: Path):
        # Google can finalize a new upload's metadata while it is first read.
        # Retry the entire read, never hand a mixed version to a row operation.
        for _ in range(3):
            metadata, etag = self.metadata(file_id)
            if metadata.get("trashed") or metadata.get("mimeType") != XLSX_MIME:
                raise DriveStorageError("The configured Drive file must be an existing Excel .xlsx workbook.")
            response = self.request("GET", f"{API_URL}/{file_id}", params={
                "alt": "media", "supportsAllDrives": "true",
            })
            destination.write_bytes(response.content)
            current, _ = self.metadata(file_id)
            if current.get("version") == metadata.get("version"):
                return metadata.get("version"), etag
        raise DriveStorageError("The Drive workbook changed while downloading. Retry the operation.")

    def update(self, file_id: str, source: Path, *, version: str, etag: str | None):
        current, _ = self.metadata(file_id)
        if current.get("version") != version:
            raise DriveStorageError("The Drive workbook changed during this update. Retry using its latest contents.")
        payload = source.read_bytes()
        checksum = hashlib.md5(payload).hexdigest()
        headers = {"Content-Type": XLSX_MIME}
        if etag:
            headers["If-Match"] = etag
        for attempt in range(MAX_ATTEMPTS):
            try:
                self.request("PATCH", f"{UPLOAD_URL}/{file_id}", params={
                    "uploadType": "media", "supportsAllDrives": "true",
                }, data=payload, headers=headers)
                return
            except DriveTransientError:
                # A timed-out upload may already have committed. Never blindly
                # replay it against a workbook another writer has changed.
                current, _ = self.metadata(file_id)
                if not current.get("trashed") and current.get("md5Checksum") == checksum:
                    return
                if current.get("version") != version:
                    raise DriveStorageError("The Drive workbook changed during this update. Retry using its latest contents.")
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                retry_delay(attempt + 1)

    def find(self, query: str) -> list[dict]:
        files = []
        page_token = None
        while True:
            params = {"q": query, "fields": "nextPageToken,files(id,name,mimeType,webViewLink)", "pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            result = self.request("GET", API_URL, params=params).json()
            files.extend(result.get("files", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                return files

    def create_folder(self, name: str) -> dict:
        return self.request("POST", API_URL, json={
            "name": name, "mimeType": "application/vnd.google-apps.folder",
            "appProperties": {"jobAgentStorage": "workbooks-v1"},
        }, params={"fields": "id,name,webViewLink"}).json()

    def create_workbook(self, source: Path, folder_id: str) -> dict:
        boundary = uuid4().hex
        metadata = json.dumps({"name": source.name, "parents": [folder_id], "mimeType": XLSX_MIME})
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{metadata}\r\n"
            f"--{boundary}\r\nContent-Type: {XLSX_MIME}\r\n\r\n"
        ).encode() + source.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        return self.request("POST", UPLOAD_URL, params={
            "uploadType": "multipart", "fields": "id,name,webViewLink",
        }, data=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"}).json()
