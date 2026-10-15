import asyncio
import os
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from mcp_connection.mcp_manager import MCPManager, chrome_server_config, silent_stdio_session


class RuntimeTests(unittest.TestCase):
    def test_old_system_node_falls_back_to_nvm(self):
        def version(args, **kwargs):
            return CompletedProcess(args, 0, 'v22.5.1' if args[0] == '/usr/local/bin/node' else 'v24.21.0', '')
        with patch.dict(os.environ, {}, clear=True), patch('shutil.which', return_value='/usr/local/bin/node'), patch.object(Path, 'glob', return_value=[Path('/nvm/v24/bin/node')]), patch.object(Path, 'is_file', return_value=True), patch('mcp_connection.mcp_manager.run', side_effect=version):
            config = chrome_server_config()
        self.assertEqual(config['command'], '/nvm/v24/bin/npx')
        self.assertTrue(config['env']['PATH'].startswith('/nvm/v24/bin:'))

    def test_explicit_unsupported_runtime_has_actionable_error(self):
        with patch.dict(os.environ, {'JOB_AGENT_NODE_BINARY': '/old/node'}), patch('mcp_connection.mcp_manager.run', return_value=CompletedProcess([], 0, 'v22.5.1', '')):
            with self.assertRaisesRegex(RuntimeError, 'JOB_AGENT_NODE_BINARY'):
                chrome_server_config()


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_stderr_is_preserved(self):
        config = {'command': sys.executable, 'args': ['-c', 'import sys; print("unsupported runtime", file=sys.stderr); sys.exit(1)']}
        with self.assertRaisesRegex(RuntimeError, 'unsupported runtime'):
            async with silent_stdio_session(config):
                self.fail('Failed server should not initialize')

    async def test_failed_start_can_retry_and_shutdown_does_not_repeat_error(self):
        with patch.dict(os.environ, {"JOB_AGENT_BROWSER_MCP": "chrome"}):
            manager = MCPManager()
        extension = SimpleNamespace(name="trigger_extension_action")
        attempts = 0

        @asynccontextmanager
        async def session(config):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError('startup failed')
            yield object()

        with patch('mcp_connection.mcp_manager.ensure_chrome_debugger', new_callable=AsyncMock), patch('mcp_connection.mcp_manager.chrome_server_config', return_value={}), patch('mcp_connection.mcp_manager.silent_stdio_session', session), patch('mcp_connection.mcp_manager.load_mcp_tools', new_callable=AsyncMock, return_value=[extension, SimpleNamespace(name="take_heapsnapshot"), SimpleNamespace(name="navigate_page")]):
            with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                await manager.start(['chrome'])
            await manager.stop()
            self.assertEqual(await manager.start(['chrome']), [extension])
            await manager.stop()
        self.assertEqual(attempts, 2)


    async def test_missing_allowed_tool_fails_closed(self):
        with patch.dict(os.environ, {"JOB_AGENT_BROWSER_MCP": "chrome"}):
            manager = MCPManager()
        @asynccontextmanager
        async def session(config):
            yield object()
        with patch('mcp_connection.mcp_manager.ensure_chrome_debugger', new_callable=AsyncMock), patch('mcp_connection.mcp_manager.chrome_server_config', return_value={}), patch('mcp_connection.mcp_manager.silent_stdio_session', session), patch('mcp_connection.mcp_manager.load_mcp_tools', new_callable=AsyncMock, return_value=[SimpleNamespace(name="take_heapsnapshot")]):
            with self.assertRaisesRegex(RuntimeError, "trigger_extension_action"):
                await manager.start(["chrome"])
            self.assertEqual(manager.chrome_tools, [])
            await manager.stop()


class PlaywrightTests(unittest.IsolatedAsyncioTestCase):
    async def test_playwright_selection_reuses_one_session_and_closes(self):
        with patch.dict(os.environ, {"JOB_AGENT_BROWSER_MCP": "playwright"}):
            manager = MCPManager()
        closed = []
        @asynccontextmanager
        async def session(config):
            yield object()
            closed.append(True)
        with patch('mcp_connection.mcp_manager._chrome_debugger_is_ready', return_value=True), patch('mcp_connection.mcp_manager.ensure_chrome_debugger', new_callable=AsyncMock), patch('mcp_connection.mcp_manager.chrome_server_config', return_value={}) as config, patch('mcp_connection.mcp_manager.silent_stdio_session', session), patch('mcp_connection.mcp_manager.load_mcp_tools', new_callable=AsyncMock, return_value=['browser_navigate']):
            self.assertEqual(await manager.start(['chrome']), ['browser_navigate'])
            self.assertEqual(await manager.start(['chrome']), ['browser_navigate'])
            await manager.stop()
        config.assert_called_once_with('playwright')
        self.assertEqual(closed, [True])

    def test_invalid_backend_fails_early(self):
        with patch.dict(os.environ, {"JOB_AGENT_BROWSER_MCP": "typo"}):
            with self.assertRaisesRegex(ValueError, 'JOB_AGENT_BROWSER_MCP'):
                MCPManager()

    def test_playwright_prompt_has_tool_schema_guidance(self):
        from prompts import load_prompt
        with patch.dict(os.environ, {"JOB_AGENT_BROWSER_MCP": "playwright"}):
            prompt = load_prompt('perform_login/system.txt')
        self.assertIn('browser_tabs', prompt)
        self.assertIn('do not pass Chrome pageId', prompt)
