import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage, ToolMessage
from openpyxl import Workbook
from workbook.jobs_workbook import find_job_rows_by_canonical_url
from nodes import is_job_valid


class JobValidityDuplicateCheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.applied_path = Path(self.directory.name) / "applied_jobs.xlsx"

    def create_applied_workbook(self, rows):
        book = Workbook()
        sheet = book.active
        sheet.title = "Applications"
        for row in rows:
            sheet.append(row)
        book.save(self.applied_path)
        book.close()

    def test_find_job_rows_by_canonical_url_matches(self):
        url = "https://www.linkedin.com/jobs/view/12345678"
        self.create_applied_workbook([
            ["id", "link", "applyUrl", "companyName", "title"],
            ["linkedin:12345678", "https://www.linkedin.com/jobs/view/12345678?refId=abc", "https://example.com/apply", "Company", "Engineer"]
        ])
        matches = find_job_rows_by_canonical_url(self.applied_path, url)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], "linkedin:12345678")

    async def test_is_job_valid_returns_not_eligible_when_url_in_applied_jobs(self):
        url = "https://www.linkedin.com/jobs/view/12345678"
        self.create_applied_workbook([
            ["id", "link", "applyUrl"],
            ["linkedin:12345678", url, url]
        ])

        state = {
            "job_url": url,
            "applied_jobs_path": str(self.applied_path),
            "is_job_valid_messages": []
        }

        result = await is_job_valid(state)

        self.assertEqual(result["validation_phase"], "NOT_ELIGIBLE")
        self.assertIn("already exists in applied_jobs.xlsx", result["final_status_message"])
        self.assertFalse(result["is_applied"])

    @patch("nodes.get_custom_model_with_tools")
    @patch("nodes.get_custom_structured_model")
    async def test_is_job_valid_proceeds_if_url_not_in_applied_jobs(self, mock_structured_model, mock_model_tools):
        url = "https://www.linkedin.com/jobs/view/99999999"
        self.create_applied_workbook([
            ["id", "link", "applyUrl"],
            ["linkedin:12345678", "https://www.linkedin.com/jobs/view/12345678", "https://www.linkedin.com/jobs/view/12345678"]
        ])

        state = {
            "job_url": url,
            "applied_jobs_path": str(self.applied_path),
            "is_job_valid_messages": [{"role": "system", "content": "system prompt"}]
        }

        tool_call = {"name": "take_snapshot", "args": {}, "id": "snapshot-1", "type": "tool_call"}
        state["is_job_valid_messages"].extend([
            {"role": "user", "content": "Inspect this job."},
            AIMessage(content="", tool_calls=[tool_call]),
            ToolMessage(content="US remote role; OPT supported.", tool_call_id="snapshot-1"),
        ])
        response = AIMessage(content="The job is eligible.")
        model = Mock(ainvoke=AsyncMock(return_value=response))

        with patch("nodes.get_custom_model_with_tools", AsyncMock(return_value=model)), \
             patch("job_nodes.use_jev", return_value={
                 "valid_job": {"choice": "ELIGIBLE"},
                 "invalid_job_reason": {"choice": "OTHER"},
             }) as evaluate:
            result = await is_job_valid(state)

        self.assertEqual(result["validation_phase"], "ELIGIBLE")
        self.assertFalse(result["is_applied"])
        conversation = json.loads(evaluate.call_args.args[0])["conversation"]
        self.assertEqual([message["role"] for message in conversation],
                         ["system", "user", "ai", "tool", "ai"])
        self.assertEqual(conversation[0], state["is_job_valid_messages"][0])
        self.assertEqual(conversation[3]["content"], "US remote role; OPT supported.")
        self.assertEqual(conversation[-1]["content"], response.content)
        model.ainvoke.assert_awaited_once_with(state["is_job_valid_messages"])
