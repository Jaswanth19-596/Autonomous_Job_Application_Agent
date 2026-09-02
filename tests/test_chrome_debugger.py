import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from mcp_connection import mcp_manager


class ChromeDebuggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_does_not_start_chrome_when_devtools_is_ready(self):
        with (
            patch.object(mcp_manager, "_chrome_debugger_is_ready", return_value=True),
            patch.object(mcp_manager, "Popen") as popen,
        ):
            await mcp_manager.ensure_chrome_debugger()

        popen.assert_not_called()

    async def test_starts_chrome_and_waits_for_devtools(self):
        with (
            patch.object(mcp_manager.Path, "is_file", return_value=True),
            patch.object(mcp_manager.Path, "mkdir"),
            patch.object(
                mcp_manager,
                "_chrome_debugger_is_ready",
                side_effect=[False, True],
            ),
            patch.object(mcp_manager, "Popen", return_value=MagicMock()) as popen,
        ):
            await mcp_manager.ensure_chrome_debugger()

        command = popen.call_args.args[0]
        self.assertIn(f"--remote-debugging-port={mcp_manager.CHROME_DEBUG_PORT}", command)
        self.assertIn("--remote-debugging-address=127.0.0.1", command)

    async def test_restarts_a_stale_chrome_session_before_loading_tools(self):
        manager = mcp_manager.MCPManager()
        manager.chrome_session = MagicMock()
        manager._chrome_task = MagicMock()
        manager._chrome_stop = MagicMock()

        async def clear_stale_session():
            manager.chrome_session = None

        with (
            patch.object(mcp_manager, "_chrome_debugger_is_ready", return_value=False),
            patch.object(
                manager,
                "_stop_chrome_session",
                new_callable=AsyncMock,
                side_effect=clear_stale_session,
            ) as stop,
            patch.object(manager, "_ensure_chrome_session", new_callable=AsyncMock) as ensure,
            patch.object(manager, "get_tools", return_value=[]),
        ):
            await manager.start(["chrome"])

        stop.assert_awaited_once()
        ensure.assert_awaited_once()
