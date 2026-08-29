from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from contextlib import asynccontextmanager
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
    """Start the browser MCP server without forwarding its diagnostics to the CLI."""
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
                "--autoConnect",
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


class MCPManager():

    def __init__(self, servers_to_use):
        self.mcp_client = MultiServerMCPClient(SERVERS)
        self.chrome_session_context = None
        self.gmail_session_context = None
        self.chrome_session = None
        self.gmail_session = None
        self.gmail_tools = []
        self.chrome_tools = []
        self.servers_to_use = servers_to_use

    async def start(self):
        if "chrome" in self.servers_to_use:
            self.chrome_session_context = silent_stdio_session(SERVERS["chrome"])
            self.chrome_session = await self.chrome_session_context.__aenter__()
            self.chrome_tools = await load_mcp_tools(self.chrome_session)
        if "gmail" in self.servers_to_use:
            self.mcp_client.connections["gmail"] = get_gmail_server_config()
            self.gmail_session_context = self.mcp_client.session("gmail")
            self.gmail_session = await self.gmail_session_context.__aenter__()
            self.gmail_tools = await load_mcp_tools(self.gmail_session)

        self.tools = self.chrome_tools + self.gmail_tools

        return self.tools

    async def stop(self):
        if self.chrome_session_context:
            await self.chrome_session_context.__aexit__(None,None,None)
        if self.gmail_session_context:
            await self.gmail_session_context.__aexit__(None,None,None)
        self.chrome_session = None
        self.gmail_session = None
        self.chrome_session_context = None
        self.gmail_session_context = None
        self.tools = None
