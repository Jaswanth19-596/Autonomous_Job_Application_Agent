import asyncio
import os
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from mcp_connection.mcp_manager import MCPManager, chrome_server_config, silent_stdio_session, close_chrome_debugger, CHROME_DEBUG_URL


class BrowserLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_absent_browser_is_not_launched_for_cleanup(self):
        with patch('mcp_connection.mcp_manager._chrome_debugger_is_ready', return_value=False), patch('playwright.async_api.async_playwright') as factory:
            await close_chrome_debugger()
        factory.assert_not_called()

    async def test_close_targets_browser_endpoint_and_waits_for_exit(self):
        session = SimpleNamespace(send=AsyncMock())
        browser = SimpleNamespace(new_browser_cdp_session=AsyncMock(return_value=session))
        chromium = SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser))
        context = AsyncMock()
        context.__aenter__.return_value = SimpleNamespace(chromium=chromium)
        with patch('playwright.async_api.async_playwright', return_value=context), patch('mcp_connection.mcp_manager._chrome_debugger_is_ready', side_effect=[True, True, False]), patch('mcp_connection.mcp_manager.asyncio.sleep', new_callable=AsyncMock):
            await close_chrome_debugger()
        chromium.connect_over_cdp.assert_awaited_once_with(CHROME_DEBUG_URL, timeout=10_000)
        session.send.assert_awaited_once_with('Browser.close')

    async def test_shutdown_failure_is_not_silently_ignored(self):
        session = SimpleNamespace(send=AsyncMock())
        browser = SimpleNamespace(new_browser_cdp_session=AsyncMock(return_value=session))
        context = AsyncMock()
        context.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=AsyncMock(return_value=browser)))
        with patch('playwright.async_api.async_playwright', return_value=context), patch('mcp_connection.mcp_manager._chrome_debugger_is_ready', return_value=True), patch('mcp_connection.mcp_manager.asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaisesRegex(RuntimeError, 'next job was not started'):
                await close_chrome_debugger()

    async def test_each_job_gets_fresh_session_and_gmail_is_untouched(self):
        for backend in ('chrome', 'playwright'):
            with patch.dict(os.environ, {'JOB_AGENT_BROWSER_MCP': backend}):
                manager = MCPManager()
            events = []
            gmail = object()
            manager.gmail_session = gmail
            tool = SimpleNamespace(name='trigger_extension_action')

            @asynccontextmanager
            async def session(config):
                events.append('start')
                yield object()
                events.append('stop')

            async def close():
                events.append('close')

            with patch('mcp_connection.mcp_manager.ensure_chrome_debugger', new_callable=AsyncMock) as launch, patch('mcp_connection.mcp_manager.chrome_server_config', return_value={}), patch('mcp_connection.mcp_manager.silent_stdio_session', session), patch('mcp_connection.mcp_manager.load_mcp_tools', new_callable=AsyncMock, return_value=[tool]), patch('mcp_connection.mcp_manager.close_chrome_debugger', side_effect=close):
                await manager.start(['chrome'])
                await manager.close_browser()
                self.assertEqual(manager.chrome_tools, [])
                self.assertIsNone(manager.chrome_session)
                self.assertIs(manager.gmail_session, gmail)
                await manager.start(['chrome'])
                await manager.close_browser()
                self.assertEqual(launch.await_count, 2)
            self.assertEqual(events, ['start', 'stop', 'close', 'start', 'stop', 'close'])


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
