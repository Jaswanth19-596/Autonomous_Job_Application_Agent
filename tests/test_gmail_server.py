import base64
import unittest
from unittest.mock import MagicMock, patch

from mcp_connection import gmail_server


class GmailStandardApiTests(unittest.TestCase):
    def test_urls_use_the_stable_gmail_rest_api(self):
        url = gmail_server._gmail_api_url(
            "users/me/messages", q="application received", maxResults=5
        )

        self.assertEqual(
            url,
            "https://gmail.googleapis.com/gmail/v1/users/me/messages?"
            "q=application+received&maxResults=5",
        )
        self.assertNotIn("gmailmcp.googleapis.com", url)

    def test_request_uses_bearer_token_with_stable_api_url(self):
        credentials = MagicMock(token="access-token")
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with (
            patch.object(gmail_server, "get_gmail_credentials", return_value=credentials),
            patch.object(gmail_server, "urlopen", return_value=response) as open_request,
            patch.object(gmail_server.json, "load", return_value={"messages": []}),
        ):
            result = gmail_server._gmail_request("users/me/messages", maxResults=1)

        self.assertEqual(result, {"messages": []})
        request = open_request.call_args.args[0]
        self.assertEqual(request.full_url, "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=1")
        self.assertEqual(request.get_header("Authorization"), "Bearer access-token")

    def test_extracts_plain_text_body_without_attachment_content(self):
        encoded = base64.urlsafe_b64encode(b"Application received.").decode().rstrip("=")
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": encoded}},
                {"mimeType": "application/pdf", "body": {"attachmentId": "abc"}},
            ],
        }

        self.assertEqual(gmail_server._plain_text_parts(payload), ["Application received."])
