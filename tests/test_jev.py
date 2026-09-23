import unittest
from unittest.mock import Mock, patch

import requests

from utilities.jev import use_jev


class JevResponseTests(unittest.TestCase):
    questions = {
        "action": {"type": "choice", "criteria": {"CHAT": "Keep chatting"}},
    }

    def call_with(self, payload, status=200, json_error=None):
        response = Mock(status_code=status)
        response.json.return_value = payload
        response.json.side_effect = json_error
        if status >= 400:
            response.raise_for_status.side_effect = requests.HTTPError(str(status))
        with patch("utilities.jev.requests.post", return_value=response):
            return use_jev("test conversation", self.questions)

    def test_success_preserves_nested_answers(self):
        answers = {"action": {"choice": "CHAT", "probabilities": {"CHAT": 1.0}}}
        self.assertEqual(self.call_with({"answers": answers}), answers)

    def test_api_error_reports_status_and_message(self):
        for status in (200, 400, 429, 503):
            with self.subTest(status=status):
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}.*Provider unavailable"):
                    self.call_with({"error": {"message": "Provider unavailable"}}, status)

    def test_http_failure_without_error_object(self):
        with self.assertRaises(requests.HTTPError):
            self.call_with({}, 502)

    def test_missing_or_malformed_answers(self):
        for payload in ({}, [], {"answers": None}, {"answers": []}):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "missing an 'answers' object"):
                    self.call_with(payload)

    def test_missing_question_or_invalid_choice(self):
        for answers in ({}, {"action": {}}, {"action": {"choice": "UNKNOWN"}}):
            with self.subTest(answers=answers):
                with self.assertRaises(ValueError):
                    self.call_with({"answers": answers})

    def test_non_json_success(self):
        with self.assertRaisesRegex(ValueError, "non-JSON"):
            self.call_with(None, json_error=ValueError("Invalid JSON"))

    def test_non_json_http_failure(self):
        with self.assertRaises(requests.HTTPError):
            self.call_with(None, 502, ValueError("Invalid JSON"))
