import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_connection import mcp_manager


class GmailCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_path = Path(self.temp_dir.name) / "gmail_token.json"
        self.token_path.write_text("cached-token", encoding="utf-8")
        self.token_patch = patch.object(mcp_manager, "GMAIL_TOKEN_PATH", self.token_path)
        self.token_patch.start()

    def tearDown(self):
        self.token_patch.stop()
        self.temp_dir.cleanup()

    def test_reuses_valid_cached_credentials_without_browser_login(self):
        credentials = MagicMock(valid=True)
        credentials.token = "cached-access-token"

        with (
            patch.object(
                mcp_manager.Credentials,
                "from_authorized_user_file",
                return_value=credentials,
            ) as load,
            patch.object(mcp_manager.InstalledAppFlow, "from_client_config") as flow,
        ):
            config = mcp_manager.get_gmail_server_config()

        load.assert_called_once_with(str(self.token_path), mcp_manager.SCOPES)
        flow.assert_not_called()
        self.assertEqual(config["transport"], "stdio")
        self.assertEqual(config["command"], mcp_manager.sys.executable)
        self.assertEqual(config["args"], ["-m", "mcp_connection.gmail_server"])
        self.assertNotIn("gmailmcp.googleapis.com", str(config))

    def test_refreshes_expired_cached_credentials_without_browser_login(self):
        credentials = MagicMock(valid=False, expired=True, refresh_token="refresh-token")
        credentials.token = "refreshed-access-token"
        credentials.to_json.return_value = '{"token":"refreshed-access-token"}'

        with (
            patch.object(
                mcp_manager.Credentials,
                "from_authorized_user_file",
                return_value=credentials,
            ),
            patch.object(mcp_manager.InstalledAppFlow, "from_client_config") as flow,
        ):
            config = mcp_manager.get_gmail_server_config()

        credentials.refresh.assert_called_once()
        flow.assert_not_called()
        self.assertEqual(self.token_path.read_text(encoding="utf-8"), credentials.to_json())
        self.assertEqual(config["transport"], "stdio")
        self.assertNotIn("gmailmcp.googleapis.com", str(config))

    def test_authorizes_and_saves_credentials_when_no_cache_exists(self):
        self.token_path.unlink()
        credentials = MagicMock()
        credentials.token = "new-access-token"
        credentials.to_json.return_value = '{"token":"new-access-token"}'
        flow = MagicMock()
        flow.run_local_server.return_value = credentials

        with patch.object(
            mcp_manager.InstalledAppFlow,
            "from_client_config",
            return_value=flow,
        ):
            config = mcp_manager.get_gmail_server_config()

        flow.run_local_server.assert_called_once_with(
            port=0,
            access_type="offline",
            prompt="consent",
        )
        self.assertEqual(self.token_path.read_text(encoding="utf-8"), credentials.to_json())
        self.assertEqual(
            self.token_path.stat().st_mode & 0o777,
            0o600,
        )
        self.assertEqual(config["transport"], "stdio")
        self.assertNotIn("gmailmcp.googleapis.com", str(config))
