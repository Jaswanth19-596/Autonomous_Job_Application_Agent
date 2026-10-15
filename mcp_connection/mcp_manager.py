import asyncio
import json
import sys
import shutil
from contextlib import asynccontextmanager
from subprocess import DEVNULL, Popen, TimeoutExpired, run
from tempfile import TemporaryFile

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from mcp import types
import asyncio

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROME_BINARY = Path(
    os.getenv(
        "JOB_AGENT_CHROME_BINARY",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
)
# Keep the agent's DevTools instance separate from normal Chrome and any other
# local automation that may use Chrome's conventional debugging port.
CHROME_DEBUG_PORT = int(os.getenv("JOB_AGENT_CHROME_PORT", "9223"))
CHROME_DEBUG_URL = f"http://127.0.0.1:{CHROME_DEBUG_PORT}"
CHROME_DEBUG_PROFILE = Path(
    os.getenv("JOB_AGENT_CHROME_PROFILE", str(Path.home() / "chrome-debug-profile"))
)
GMAIL_TOKEN_PATH = Path(
    os.getenv("JOB_AGENT_GMAIL_TOKEN_PATH", str(PROJECT_ROOT / "user" / "gmail_token.json"))
)


async def list_workspace_roots(_: object) -> types.ListRootsResult:
    """Allow Chrome DevTools MCP to read only files from this project."""
    return types.ListRootsResult(
        roots=[
            types.Root(
                uri=PROJECT_ROOT.as_uri(),
                name=PROJECT_ROOT.name,
            )
        ]
    )


def chrome_server_config():
    """Use a supported Node runtime even when launched outside an nvm shell."""
    configured = os.getenv("JOB_AGENT_NODE_BINARY")
    current = shutil.which("node")
    candidates = [Path(configured)] if configured else (
        ([Path(current)] if current else [])
        + sorted((Path.home() / ".nvm/versions/node").glob("*/bin/node"), reverse=True)
    )
    for node in candidates:
        try:
            result = run([str(node), "--version"], capture_output=True, text=True, timeout=5)
            major, minor, *_ = map(int, result.stdout.strip().lstrip("v").split("."))
            if result.returncode or not (
                (major == 20 and minor >= 19) or (major == 22 and minor >= 12)
                or (major >= 24 and major % 2 == 0)
            ):
                continue
        except (OSError, ValueError, TimeoutExpired):
            continue
        npx = node.parent / "npx"
        if not npx.is_file():
            continue
        config = dict(SERVERS["chrome"])
        config["command"] = str(npx)
        config["env"] = {**os.environ, "PATH": str(node.parent) + os.pathsep + os.environ.get("PATH", "")}
        return config
    raise RuntimeError(
        "Browser MCP requires Node 20.19+, 22.12+, or a newer LTS with npx. "
        "Set JOB_AGENT_NODE_BINARY to a compatible Node executable."
    )


@asynccontextmanager
async def silent_stdio_session(server_config):
    """Create one Chrome MCP session without forwarding server diagnostics."""
    params = StdioServerParameters(
        command=server_config["command"],
        args=server_config["args"],
        env=server_config.get("env"),
        cwd=server_config.get("cwd"),
    )
    # Keep diagnostics quiet on success, but preserve startup failures.
    with TemporaryFile(mode="w+b") as stderr:
        initialized = False
        try:
            async with stdio_client(params, errlog=stderr) as (read, write):
                async with ClientSession(
                    read,
                    write,
                    list_roots_callback=list_workspace_roots,
                ) as session:
                    await session.initialize()
                    initialized = True
                    yield session
        except Exception as exc:
            if initialized:
                raise
            stderr.seek(0, os.SEEK_END)
            size = stderr.tell()
            stderr.seek(max(0, size - 8192))
            diagnostics = stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                "Browser MCP server failed to initialize. "
                + (diagnostics or str(exc))
            ) from exc


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

client_config = {
    "installed": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),

        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",

        "redirect_uris": [
            "http://localhost"
        ],
    }
}

SERVERS = {
        "chrome": {
            "transport": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "chrome-devtools-mcp@latest",
                f"--browserUrl={CHROME_DEBUG_URL}",
                "--screenshot-format=jpeg",
                "--screenshot-quality=50",
                "--screenshot-max-width=1280",
                "--no-usage-statistics",
                "--categoryExtensions=true"
            ],
        },
}


def _chrome_debugger_is_ready() -> bool:
    """Return whether the configured endpoint is a Chrome DevTools server."""
    try:
        with urlopen(f"{CHROME_DEBUG_URL}/json/version", timeout=1) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False
    return bool(payload.get("webSocketDebuggerUrl"))


async def ensure_chrome_debugger() -> None:
    """Start the dedicated Chrome profile and wait until DevTools is reachable."""
    if await asyncio.to_thread(_chrome_debugger_is_ready):
        return

    if not CHROME_BINARY.is_file():
        raise RuntimeError(
            f"Google Chrome was not found at {CHROME_BINARY}. "
            "Set JOB_AGENT_CHROME_BINARY to its executable path."
        )

    CHROME_DEBUG_PROFILE.mkdir(parents=True, exist_ok=True)
    Popen(
        [
            str(CHROME_BINARY),
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={CHROME_DEBUG_PORT}",
            f"--user-data-dir={CHROME_DEBUG_PROFILE}",
        ],
        stdout=DEVNULL,
        stderr=DEVNULL,
        start_new_session=True,
    )

    for _ in range(20):
        if await asyncio.to_thread(_chrome_debugger_is_ready):
            return
        await asyncio.sleep(0.5)

    raise RuntimeError(
        f"Chrome did not expose DevTools at {CHROME_DEBUG_URL}/json/version. "
        f"Port {CHROME_DEBUG_PORT} may be in use by another process."
    )


async def close_chrome_debugger() -> None:
    """Gracefully exit only Chrome at the agent's dedicated debugging endpoint.

    Browser.close exits the entire browser, including extension processes;
    closing tabs or disconnecting a CDP client does not. Keep the profile on
    disk so the next launch can reuse saved authentication.
    """
    if not await asyncio.to_thread(_chrome_debugger_is_ready):
        return

    from playwright.async_api import Error, async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(
            CHROME_DEBUG_URL, timeout=10_000
        )
        session = await browser.new_browser_cdp_session()
        try:
            await asyncio.wait_for(session.send("Browser.close"), timeout=10)
        except Error:
            # Chrome can disconnect before acknowledging its own shutdown.
            if browser.is_connected():
                raise

    for _ in range(40):
        if not await asyncio.to_thread(_chrome_debugger_is_ready):
            return
        await asyncio.sleep(0.25)
    raise RuntimeError("Dedicated Chrome did not shut down; next job was not started.")


def _save_gmail_credentials(credentials: Credentials) -> None:
    """Persist credentials with owner-only file permissions."""
    GMAIL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GMAIL_TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    GMAIL_TOKEN_PATH.chmod(0o600)


def get_gmail_credentials() -> Credentials:
    """Load, refresh, or (only when necessary) authorize Gmail credentials."""
    credentials = None
    if GMAIL_TOKEN_PATH.is_file():
        try:
            credentials = Credentials.from_authorized_user_file(
                str(GMAIL_TOKEN_PATH), SCOPES
            )
        except (OSError, ValueError):
            # A truncated or manually edited cache cannot be trusted. Re-authorize
            # and replace it instead of failing all Gmail-backed workflows.
            credentials = None

    if credentials and credentials.valid:
        return credentials

    if (
        credentials
        and credentials.expired
        and credentials.refresh_token
    ):
        try:
            credentials.refresh(Request())
            _save_gmail_credentials(credentials)
            return credentials
        except RefreshError:
            # The user revoked access or Google invalidated the refresh token.
            # Fall through to an interactive authorization.
            pass

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    credentials = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )
    _save_gmail_credentials(credentials)
    return credentials


def get_gmail_server_config():
    """Configure the local adapter backed by the stable Gmail REST API v1.

    ``gmail.googleapis.com`` exposes Gmail's REST API, not an MCP endpoint.
    Running a small local stdio server lets the workflow retain its MCP tool
    interface without depending on the Workspace Developer Preview server at
    ``gmailmcp.googleapis.com``.
    """
    # Authorize (and refresh) before the server is started so OAuth failures
    # surface at the workflow boundary rather than during an arbitrary tool
    # call. The refreshed credentials are saved to GMAIL_TOKEN_PATH; the child
    # process reads that file directly and never receives a token via its args.
    get_gmail_credentials()
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "mcp_connection.gmail_server"],
        "cwd": str(PROJECT_ROOT),
    }


class MCPManager:
    """Provide persistent MCP tools while keeping session ownership task-local."""

    def __init__(self):
        self.mcp_client = MultiServerMCPClient(SERVERS)
        self.gmail_tools = []
        self.chrome_tools = []
        self.chrome_session = None
        self.gmail_session = None
        self._chrome_task = None
        self._gmail_task = None
        self._chrome_ready = None
        self._gmail_ready = None
        self._chrome_stop = None
        self._gmail_stop = None
        self._chrome_error = None
        self._gmail_error = None

    async def _run_chrome_session(self):
        """Own Chrome's context manager for its entire lifetime."""
        try:
            config = await asyncio.to_thread(chrome_server_config)
            async with silent_stdio_session(config) as session:
                self.chrome_session = session
                self.chrome_tools = await load_mcp_tools(session)
                self._chrome_ready.set()
                await self._chrome_stop.wait()
        except BaseException as exc:
            self._chrome_error = exc
            if self._chrome_ready is not None:
                self._chrome_ready.set()
            raise
        finally:
            self.chrome_session = None

    async def _run_gmail_session(self):
        """Own Gmail's context manager for its entire lifetime."""
        try:
            async with self.mcp_client.session("gmail") as session:
                self.gmail_session = session
                self.gmail_tools = await load_mcp_tools(session)
                self._gmail_ready.set()
                await self._gmail_stop.wait()
        except BaseException as exc:
            self._gmail_error = exc
            if self._gmail_ready is not None:
                self._gmail_ready.set()
            raise
        finally:
            self.gmail_session = None

    async def _ensure_chrome_session(self):
        if self._chrome_task is None:
            await ensure_chrome_debugger()
            self._chrome_ready = asyncio.Event()
            self._chrome_stop = asyncio.Event()
            self._chrome_error = None
            self._chrome_task = asyncio.create_task(
                self._run_chrome_session(), name="chrome-mcp-session"
            )

        await self._chrome_ready.wait()
        if self._chrome_error is not None:
            error = self._chrome_error
            await self._stop_chrome_session()
            raise error

    async def _stop_chrome_session(self):
        """Close a stale Chrome MCP session without disturbing Gmail."""
        task = self._chrome_task
        stop_event = self._chrome_stop
        if task is None or stop_event is None:
            return

        stop_event.set()
        try:
            await task
        except BaseException:
            # A broken browser session is being replaced, so its shutdown
            # exception is not useful to callers.
            pass
        finally:
            self.chrome_tools = []
            self.chrome_session = None
            self._chrome_task = None
            self._chrome_ready = None
            self._chrome_stop = None
            self._chrome_error = None

    async def close_browser(self):
        """Release MCP collectors, then exit the dedicated browser between jobs."""
        await self._stop_chrome_session()
        await close_chrome_debugger()

    async def _ensure_gmail_session(self):
        if self._gmail_task is None:
            self._gmail_ready = asyncio.Event()
            self._gmail_stop = asyncio.Event()
            self._gmail_error = None
            self._gmail_task = asyncio.create_task(
                self._run_gmail_session(), name="gmail-mcp-session"
            )

        await self._gmail_ready.wait()
        if self._gmail_error is not None:
            await self._gmail_task

    async def start(self, servers_to_use: list[str]) -> list:
        """Start requested persistent sessions in manager-owned tasks."""
        if "chrome" in servers_to_use:
            if self.chrome_session is not None and not await asyncio.to_thread(
                _chrome_debugger_is_ready
            ):
                await self._stop_chrome_session()
            if self.chrome_session is None:
                await self._ensure_chrome_session()

        if "gmail" in servers_to_use and self.gmail_session is None:
            self.mcp_client.connections["gmail"] = get_gmail_server_config()
            await self._ensure_gmail_session()

        return self.get_tools(servers_to_use)


    async def stop(self):
        """Ask each owner task to close its own persistent MCP session."""
        errors = []

        for task, stop_event in (
            (self.chrome_task, self._chrome_stop),
            (self.gmail_task, self._gmail_stop),
        ):
            if task is None:
                continue
            stop_event.set()
            try:
                await task
            except BaseException as exc:
                errors.append(exc)

        self.chrome_tools = []
        self.gmail_tools = []
        self._chrome_task = None
        self._gmail_task = None
        self._chrome_ready = None
        self._gmail_ready = None
        self._chrome_stop = None
        self._gmail_stop = None
        self.mcp_client.connections.pop("gmail", None)

        if errors:
            raise errors[0]

    @property
    def chrome_task(self):
        return self._chrome_task

    @property
    def gmail_task(self):
        return self._gmail_task


    def get_tools(self, servers_to_use: list[str]) -> list:
        tools = []
        if "chrome" in servers_to_use:
            tools += self.chrome_tools
        if "gmail" in servers_to_use:
            tools += self.gmail_tools
        return tools
