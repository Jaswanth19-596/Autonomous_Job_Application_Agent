import asyncio
from contextlib import asynccontextmanager

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import os
from pathlib import Path
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow
from mcp import types

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


@asynccontextmanager
async def silent_stdio_session(server_config):
    """Create one Chrome MCP session without forwarding server diagnostics."""
    params = StdioServerParameters(
        command=server_config["command"],
        args=server_config["args"],
        env=server_config.get("env"),
        cwd=server_config.get("cwd"),
    )
    with open(os.devnull, "w", encoding="utf-8") as stderr:
        async with stdio_client(params, errlog=stderr) as (read, write):
            async with ClientSession(
                read,
                write,
                list_roots_callback=list_workspace_roots,
            ) as session:
                await session.initialize()
                yield session


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
                "--browserUrl=http://127.0.0.1:9222",
                "--screenshot-format=jpeg",
                "--screenshot-quality=50",
                "--screenshot-max-width=1280",
                "--no-usage-statistics",
                "--categoryExtensions=true"
            ],
        },
}


def get_gmail_server_config():
    """Authorize Gmail only when a workflow actually needs its MCP tools."""
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    credentials = flow.run_local_server(port=0)
    return {
        "transport": "streamable_http",
        "url": "https://gmailmcp.googleapis.com/mcp/v1",
        "headers": {
            "Authorization": f"Bearer {credentials.token}"
        },
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
            async with silent_stdio_session(SERVERS["chrome"]) as session:
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
            self._chrome_ready = asyncio.Event()
            self._chrome_stop = asyncio.Event()
            self._chrome_error = None
            self._chrome_task = asyncio.create_task(
                self._run_chrome_session(), name="chrome-mcp-session"
            )

        await self._chrome_ready.wait()
        if self._chrome_error is not None:
            await self._chrome_task

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
        if "chrome" in servers_to_use and self.chrome_session is None:
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
