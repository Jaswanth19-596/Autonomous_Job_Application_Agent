import json
import unittest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from utilities.diagnostics import (
    is_browser_snapshot_content,
    sanitize_browser_snapshot,
    _serialize_for_state_log,
    format_node_state,
    log_node_state,
    log_tool_calls,
    log_tool_result,
)


class DiagnosticsLoggingTests(unittest.TestCase):
    def test_browser_snapshot_identification(self):
        # Tool names
        self.assertTrue(is_browser_snapshot_content("any content", tool_name="take_snapshot"))
        self.assertTrue(is_browser_snapshot_content("any content", tool_name="take_screenshot"))
        self.assertTrue(is_browser_snapshot_content("any content", tool_name="get_page_source"))
        self.assertFalse(is_browser_snapshot_content("hello world", tool_name="generate_application_answer"))

        # Base64 images
        self.assertTrue(is_browser_snapshot_content("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD..."))

        # Accessibility tree / DOM dumps
        dom_snapshot = (
            "Page snapshot:\n"
            "- uid=1_0 RootWebArea 'Job Search'\n"
            "  - uid=1_1 heading 'Senior Software Engineer'\n"
            "  - uid=1_2 button 'Apply Now'\n"
        )
        self.assertTrue(is_browser_snapshot_content(dom_snapshot))

        # Normal text should NOT be identified as snapshot
        normal_text = "I have applied to the job and updated the status to APPLIED."
        self.assertFalse(is_browser_snapshot_content(normal_text))

    def test_state_serialization_preserves_state_and_omits_snapshots(self):
        state = {
            "job_url": "https://example.com/jobs/123",
            "validation_phase": "ELIGIBLE",
            "is_valid": True,
            "application_queue": ["https://example.com/jobs/123"],
            "user_profile": {"name": "Jaswanth", "email": "user@example.com"},
            "messages": [
                SystemMessage(content="You are a helpful assistant."),
                HumanMessage(content="Please apply to this job: https://example.com/jobs/123"),
                AIMessage(
                    content="I will inspect the job page.",
                    tool_calls=[{"name": "take_snapshot", "args": {}, "id": "call_1"}],
                ),
                ToolMessage(
                    content="Page snapshot:\n- uid=1_1 button 'Apply'\n- uid=1_2 textbox 'Email'",
                    tool_call_id="call_1",
                    name="take_snapshot",
                ),
                AIMessage(
                    content="I will generate answers.",
                    tool_calls=[{"name": "generate_application_answer", "args": {"question": "Years of experience"}, "id": "call_2"}],
                ),
                ToolMessage(
                    content="5 years of Python experience.",
                    tool_call_id="call_2",
                    name="generate_application_answer",
                ),
            ],
        }

        sanitized = format_node_state(state)

        # All state keys intact
        self.assertEqual(sanitized["job_url"], "https://example.com/jobs/123")
        self.assertEqual(sanitized["validation_phase"], "ELIGIBLE")
        self.assertTrue(sanitized["is_valid"])
        self.assertEqual(sanitized["application_queue"], ["https://example.com/jobs/123"])
        self.assertEqual(sanitized["user_profile"]["name"], "Jaswanth")

        # Messages intact except snapshot
        messages = sanitized["messages"]
        self.assertEqual(len(messages), 6)
        self.assertEqual(messages[0]["content"], "You are a helpful assistant.")
        self.assertEqual(messages[1]["content"], "Please apply to this job: https://example.com/jobs/123")
        self.assertEqual(messages[2]["content"], "I will inspect the job page.")
        # Snapshot tool message replaced with concise placeholder
        self.assertIn("[Browser Snapshot omitted", messages[3]["content"])
        self.assertEqual(messages[3]["name"], "take_snapshot")
        # Non-snapshot tool message preserved in full
        self.assertEqual(messages[5]["content"], "5 years of Python experience.")
        self.assertEqual(messages[5]["name"], "generate_application_answer")

    def test_log_only_on_state_change(self):
        state_1 = {"job_url": "https://example.com/job1", "validation_phase": "ELIGIBLE"}
        state_2 = {"job_url": "https://example.com/job1", "validation_phase": "ELIGIBLE"}
        state_3 = {"job_url": "https://example.com/job1", "validation_phase": "NOT_ELIGIBLE"}

        # First call logs state
        logged_1 = log_node_state("node_a", state_1, force=True)
        self.assertTrue(logged_1)

        # Identical state does not log
        logged_2 = log_node_state("node_b", state_2)
        self.assertFalse(logged_2)

        # Changed state logs
        logged_3 = log_node_state("node_c", state_3)
        self.assertTrue(logged_3)

    def test_llm_logger_format_message_sanitizes_snapshots(self):
        from utilities.llm_logger import _format_message

        # Snapshot tool message
        snap_msg = ToolMessage(
            content="Page snapshot:\n- uid=1_0 Root\n- uid=1_1 heading 'Engineer'",
            tool_call_id="call_99",
            name="take_snapshot",
        )
        formatted_snap = _format_message(snap_msg, 1)
        self.assertIn("[Browser Snapshot omitted", formatted_snap)
        self.assertNotIn("uid=1_0", formatted_snap)

        # Standard human message
        human_msg = HumanMessage(content="Hello assistant!")
        formatted_human = _format_message(human_msg, 2)
        self.assertIn("Hello assistant!", formatted_human)


if __name__ == "__main__":
    unittest.main()
