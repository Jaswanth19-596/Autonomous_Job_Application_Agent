# from mcp import ClientSession, StdioServerParameters
# from mcp.client.stdio import stdio_client

from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_mcp_adapters.client import MultiServerMCPClient

import asyncio

client = MultiServerMCPClient(SERVERS)

async def get_tools():
    async with client.session("chrome-devtools") as session:
        tools = await load_mcp_tools(session)
        return tools


if __name__ == "__main__":
    asyncio.run(get_tools())
