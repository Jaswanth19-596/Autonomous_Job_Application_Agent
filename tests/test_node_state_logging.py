import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from utilities.diagnostics import (
    NODE_STATE_LOG_FILE_PATH,
    format_node_state,
    log_node_state,
    trace_node,
)


class DummyJobModel(BaseModel):
    title: str
    company: str
    salary: str


class NodeStateLoggingTests(unittest.TestCase):
    def setUp(self):
        self.log_file = NODE_STATE_LOG_FILE_PATH

    def test_format_node_state_redacts_credentials(self):
        raw_state = {
            "password": "super_secret_password",
            "api_key": "sk-1234567890",
            "auth_token": "token_xyz",
            "nested": {
                "secret": "hidden_value",
                "normal": "visible_value",
            },
            "user_email": "test@example.com",
        }
        formatted = format_node_state(raw_state)
        self.assertEqual(formatted["password"], "***REDACTED***")
        self.assertEqual(formatted["api_key"], "***REDACTED***")
        self.assertEqual(formatted["nested"]["secret"], "***REDACTED***")
        self.assertEqual(formatted["nested"]["normal"], "visible_value")
        self.assertEqual(formatted["user_email"], "test@example.com")

    def test_format_node_state_serializes_pydantic_and_messages(self):
        pydantic_obj = DummyJobModel(title="Software Engineer", company="Acme Corp", salary="$150k")
        messages = [
            HumanMessage(content="Hello agent"),
            AIMessage(content="I will apply for you", tool_calls=[{"name": "fetch_job", "args": {"id": 1}, "id": "call_123"}]),
        ]
        raw_state = {
            "current_job": pydantic_obj,
            "messages": messages,
        }
        formatted = format_node_state(raw_state)
        self.assertIsInstance(formatted["current_job"], dict)
        self.assertEqual(formatted["current_job"]["title"], "Software Engineer")
        self.assertEqual(formatted["current_job"]["company"], "Acme Corp")
        self.assertEqual(len(formatted["messages"]), 2)
        self.assertEqual(formatted["messages"][0]["type"], "HumanMessage")
        self.assertEqual(formatted["messages"][0]["content"], "Hello agent")
        self.assertEqual(formatted["messages"][1]["type"], "AIMessage")
        self.assertEqual(formatted["messages"][1]["tool_calls"][0]["name"], "fetch_job")

    def test_log_node_state_writes_to_log_file(self):
        test_state = {
            "user_node_decision": "APPLY_FOR_JOBS",
            "current_job": {"job_title": "AI Engineer", "job_url": "https://example.com/job/1"},
            "application_queue": [{"id": 1}, {"id": 2}],
            "validation_phase": "ELIGIBLE",
            "resolution_phase": "SUCCESS",
            "requires_human": False,
            "is_valid": True,
        }
        log_node_state("test_node", test_state)

        self.assertTrue(self.log_file.exists())
        with open(self.log_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ENTERING NODE: test_node", content)
        self.assertIn("Decision: APPLY_FOR_JOBS", content)
        self.assertIn("Current Job: AI Engineer", content)
        self.assertIn("Application Queue: 2 job(s)", content)
        self.assertIn("validation_phase=ELIGIBLE", content)
        self.assertIn("resolution_phase=SUCCESS", content)

    def test_trace_node_sync_logs_state_on_entry(self):
        recorded_calls = []

        @trace_node
        def sync_sample_node(state):
            recorded_calls.append(state.get("step"))
            return {"step": "completed"}

        state = {"step": "step_1", "job_url": "https://example.com/job"}
        with patch("utilities.diagnostics.log_node_state") as mock_log_state:
            res = sync_sample_node(state)
            mock_log_state.assert_called_once_with("sync_sample_node", state)
            self.assertEqual(recorded_calls, ["step_1"])
            self.assertEqual(res, {"step": "completed"})

    def test_trace_node_async_logs_state_on_entry(self):
        recorded_calls = []

        @trace_node
        async def async_sample_node(state):
            recorded_calls.append(state.get("step"))
            return {"step": "async_completed"}

        state = {"step": "step_async", "user_node_decision": "CHAT"}
        with patch("utilities.diagnostics.log_node_state") as mock_log_state:
            res = asyncio.run(async_sample_node(state))
            mock_log_state.assert_called_once_with("async_sample_node", state)
            self.assertEqual(recorded_calls, ["step_async"])
            self.assertEqual(res, {"step": "async_completed"})


if __name__ == "__main__":
    unittest.main()
