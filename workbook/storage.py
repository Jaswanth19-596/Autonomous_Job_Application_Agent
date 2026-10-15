"""Resolve logical workbook paths to fresh Drive copies for each operation."""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp
import threading

import fcntl

from workbook.drive import DriveClient, DriveStorageError, PROJECT_ROOT, config_path, get_credentials


WORKBOOK_NAMES = ("pending_jobs.xlsx", "applied_jobs.xlsx", "invalid_jobs.xlsx")
_locks = {name: threading.RLock() for name in WORKBOOK_NAMES}


def drive_file_id(path: Path) -> str | None:
    """Only route the three project workbooks; temporary/custom files stay local."""
    path = Path(path).resolve()
    if path.parent != (PROJECT_ROOT / "data").resolve() or path.name not in WORKBOOK_NAMES:
        return None
    backend = os.getenv("JOB_AGENT_WORKBOOK_STORAGE", "auto").strip().lower()
    if backend not in {"auto", "drive", "local"}:
        raise DriveStorageError("JOB_AGENT_WORKBOOK_STORAGE must be auto, drive, or local.")
    if backend == "local":
        return None
    manifest = config_path()
    if not manifest.is_file():
        if backend == "auto":
            return None
        raise DriveStorageError("Drive workbooks have not been configured. Run: uv run python -m workbook.drive_setup")
    try:
        config = json.loads(manifest.read_text(encoding="utf-8"))
        file_id = config["files"][path.name]
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("Missing file ID")
        return file_id
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DriveStorageError("Invalid Drive workbook configuration; refusing to fall back to local data.") from exc


@contextmanager
def workbook_file(path: Path, *, write: bool = False):
    """Download before use and upload successful changes, with no stale fallback.

    A per-workbook lock serializes this checkout's threads and processes. Version
    checks also detect edits made on Drive during an operation. Run one writer
    across machines and avoid simultaneous interactive edits of these .xlsx files.
    """
    path = Path(path)
    file_id = drive_file_id(path)
    if file_id is None:
        yield path
        return
    lock_dir = PROJECT_ROOT / "data/.drive-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with _locks[path.name], (lock_dir / path.name).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        client = DriveClient(get_credentials())
        try:
            with TemporaryDirectory(prefix="job-agent-drive-") as directory:
                local = Path(directory) / path.name
                version, etag = client.download(file_id, local)
                original = hashlib.sha256(local.read_bytes()).digest() if write else None
                yield local
                if write and hashlib.sha256(local.read_bytes()).digest() != original:
                    try:
                        client.update(file_id, local, version=version, etag=etag)
                    except DriveStorageError as exc:
                        recovery_dir = PROJECT_ROOT / "data/.drive-recovery"
                        recovery_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                        fd, recovery = mkstemp(prefix=path.stem + "-", suffix=".xlsx", dir=recovery_dir)
                        with os.fdopen(fd, "wb") as saved:
                            saved.write(local.read_bytes())
                        raise DriveStorageError(
                            f"{exc} Unsynced workbook preserved at {recovery}."
                        ) from exc
        finally:
            client.close()
            fcntl.flock(lock, fcntl.LOCK_UN)


def workbook_exists(path: Path) -> bool:
    """A Drive error must propagate rather than make an existing workbook vanish."""
    file_id = drive_file_id(path)
    if file_id is None:
        return Path(path).is_file()
    client = DriveClient(get_credentials())
    try:
        metadata, _ = client.metadata(file_id)
        if metadata.get("trashed"):
            raise DriveStorageError("The configured Drive workbook is in the trash. Restore it before continuing.")
        return True
    finally:
        client.close()


def with_workbook(parameter: str = "path", *, write: bool = False):
    """Keep existing row operations intact while substituting their working path."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            with workbook_file(bound.arguments[parameter], write=write) as local:
                bound.arguments[parameter] = local
                return function(*bound.args, **bound.kwargs)

        return wrapped
    return decorate
