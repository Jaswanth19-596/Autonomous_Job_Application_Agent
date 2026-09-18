import unittest
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from models.schemas import ApplicationUrlResolutionResult
from nodes import resolve_application_url


class ApplicationUrlResolutionTests(unittest.IsolatedAsyncioTestCase):
    def malformed_login_error(self):
        try:
            ApplicationUrlResolutionResult.model_validate({
                "result": {"action": "REQUIRES_LOGIN"},
            })
        except ValidationError as error:
            return error
        self.fail("Login details must remain required")

    async def run_resolver(self, structured):
        browser = Mock(ainvoke=AsyncMock(return_value=Mock(tool_calls=[])))
        with patch("nodes.get_custom_model_with_tools", AsyncMock(return_value=browser)), \
             patch("nodes.get_custom_structured_model", return_value=structured):
            return await resolve_application_url({
                "resolve_application_url_messages": [{
                    "role": "user", "content": "Login gate: https://example.com/login",
                }],
                "job_url": "https://example.com/job",
                "user_profile": {},
            })

    async def test_missing_login_fields_are_repaired_before_handoff(self):
        valid = ApplicationUrlResolutionResult.model_validate({"result": {
            "action": "REQUIRES_LOGIN",
            "login_page_url": "https://example.com/login",
            "login_message": "Sign-in required to access the form",
        }})
        structured = Mock(ainvoke=AsyncMock(side_effect=[self.malformed_login_error(), valid]))
        result = await self.run_resolver(structured)
        self.assertEqual(result["actual_job_url"], "https://example.com/login")
        self.assertEqual(result["resolution_phase"], "REQUIRES_LOGIN")
        self.assertEqual(result["login_return_node"], "resolve_application_url")
        self.assertIn("Sign-in required", result["perform_login_messages"][0]["content"])
        calls = structured.ainvoke.call_args_list
        self.assertEqual(len(calls), 2)
        feedback = calls[1].args[0][-1]["content"]
        self.assertIn("login_page_url", feedback)
        self.assertIn("login_message", feedback)
        self.assertEqual(calls[1].args[0][:-1], calls[0].args[0])

    async def test_validation_retries_are_bounded(self):
        structured = Mock(ainvoke=AsyncMock(side_effect=self.malformed_login_error()))
        with self.assertRaises(ValidationError):
            await self.run_resolver(structured)
        self.assertEqual(structured.ainvoke.await_count, 4)

    async def test_empty_response_can_recover(self):
        valid = ApplicationUrlResolutionResult.model_validate({"result": {
            "action": "READY_TO_APPLY", "actual_job_url": "https://example.com/apply",
        }})
        structured = Mock(ainvoke=AsyncMock(side_effect=[None, valid]))
        result = await self.run_resolver(structured)
        self.assertEqual(result["resolution_phase"], "SUCCESS")
        self.assertEqual(result["actual_job_url"], "https://example.com/apply")

    async def test_empty_responses_are_bounded(self):
        structured = Mock(ainvoke=AsyncMock(return_value=None))
        with self.assertRaisesRegex(ValueError, "didn't produce"):
            await self.run_resolver(structured)
        self.assertEqual(structured.ainvoke.await_count, 4)
