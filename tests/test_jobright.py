import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook, load_workbook

from jobs.fetcher import JOB_COLUMNS
from jobs.jobright import (
    recent_workbook_job_summaries,
    workbook_rows_from_jobright_jobs,
)
from models import JobrightJob
import nodes
from prompts import load_prompt
from workbook.jobs_workbook import upsert_job_rows


class JobrightWorkbookRowsTests(unittest.TestCase):
    def test_returns_the_twenty_most_recent_workbook_company_role_pairs(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Applications"
            sheet.append(["companyName", "title", "application_status"])
            for number in range(1, 22):
                sheet.append([f"Company {number}", f"Role {number}", "Applied"])
            sheet.append(["Unapplied Company", "Unapplied Role", "Not Applied"])
            workbook.save(path)

            applications = recent_workbook_job_summaries(path)

        self.assertEqual(len(applications), 20)
        self.assertEqual(
            applications[0],
            {"companyName": "Unapplied Company", "title": "Unapplied Role"},
        )
        self.assertEqual(applications[-1], {"companyName": "Company 3", "title": "Role 3"})

    def test_preserves_top_matched_order_and_workbook_schema(self):
        jobs = [
            {
                "rank": rank,
                "link": f"/jobs/recommended/job-{rank}",
                "applyUrl": f"https://jobs.example.com/openings/{rank}",
                "title": f"Role {rank}",
                "companyName": f"Company {rank}",
                "location": "Remote",
                "employmentType": "Full-time",
            }
            for rank in range(1, 6)
        ]

        rows = workbook_rows_from_jobright_jobs(jobs)

        self.assertEqual([row["title"] for row in rows], [f"Role {rank}" for rank in range(1, 6)])
        self.assertEqual(set(rows[0]), set(JOB_COLUMNS))
        self.assertEqual(rows[0]["link"], "https://jobs.example.com/openings/1")
        self.assertEqual(rows[0]["applyUrl"], "https://jobs.example.com/openings/1")
        self.assertEqual(rows[0]["jobboard"], "Jobright")
        self.assertEqual(rows[0]["application_status"], "Not Applied")

    def test_rejects_any_result_set_that_is_not_the_requested_count(self):
        jobs = [{"rank": 1, "link": "https://jobright.com/jobs/1"}] * 4

        with self.assertRaisesRegex(ValueError, "top 5"):
            workbook_rows_from_jobright_jobs(jobs)

    def test_accepts_one_job_when_one_was_requested(self):
        rows = workbook_rows_from_jobright_jobs(
            [
                {
                    "rank": 1,
                    "link": "https://jobright.com/jobs/info/job-1",
                    "applyUrl": "https://jobs.example.com/openings/1",
                    "title": "Role 1",
                    "companyName": "Company 1",
                }
            ],
            expected_count=1,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Role 1")

    def test_rejects_more_jobs_than_requested(self):
        jobs = [
            {
                "rank": rank,
                "link": f"https://jobright.com/jobs/info/job-{rank}",
                "applyUrl": f"https://jobs.example.com/openings/{rank}",
                "title": f"Role {rank}",
                "companyName": f"Company {rank}",
            }
            for rank in range(1, 3)
        ]

        with self.assertRaisesRegex(ValueError, "top 1"):
            workbook_rows_from_jobright_jobs(jobs, expected_count=1)


class JobrightPromptTests(unittest.TestCase):
    def test_single_job_collection_prompts_include_iteration_and_exclusions(self):
        values = {
            "job_number": 1,
            "job_count": 1,
            "jobright_url": "https://jobright.com/jobs/recommended",
            "excluded_jobs": "- Company: Example Co; Role: Example Role",
        }

        self.assertIn(
            "exactly one Jobright Top Matched job card",
            load_prompt("fetch_jobright_jobs/system.txt", **values),
        )
        self.assertIn(
            "job 1 of 1",
            load_prompt("fetch_jobright_jobs/user.txt", **values),
        )
        self.assertIn(
            "single selected job (1 of 1)",
            load_prompt("fetch_jobright_jobs/structured_system.txt", **values),
        )
        self.assertIn(
            "These jobs must not be fetched again.",
            load_prompt("fetch_jobright_jobs/system.txt", **values),
        )

    def test_rejects_a_jobright_url_as_the_application_destination(self):
        jobs = [
            {
                "rank": rank,
                "link": f"https://jobright.com/jobs/info/job-{rank}",
                "applyUrl": f"https://jobright.com/jobs/info/job-{rank}",
                "title": f"Role {rank}",
                "companyName": f"Company {rank}",
            }
            for rank in range(1, 6)
        ]

        with self.assertRaisesRegex(ValueError, "external application URL"):
            workbook_rows_from_jobright_jobs(jobs)

    def test_batch_upsert_skips_duplicate_llm_rows_and_reuses_existing_row(self):
        row = workbook_rows_from_jobright_jobs([
            {
                "rank": rank,
                "link": f"https://jobright.ai/jobs/info/job-{rank}",
                "applyUrl": f"https://jobs.example.com/openings/{rank}",
                "title": f"Role {rank}",
                "companyName": f"Company {rank}",
            }
            for rank in range(1, 6)
        ])[0]

        with TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Applications"
            sheet.append(JOB_COLUMNS)
            workbook.save(path)

            first_write = upsert_job_rows(path, [row, row.copy()])
            second_write = upsert_job_rows(path, [row])

            self.assertEqual(first_write.inserted, 1)
            self.assertEqual(first_write.updated, 0)
            self.assertEqual(first_write.skipped_duplicates, 1)
            self.assertEqual(second_write.inserted, 0)
            self.assertEqual(second_write.updated, 1)

            saved = load_workbook(path, read_only=True)
            try:
                self.assertEqual(saved["Applications"].max_row, 2)
            finally:
                saved.close()


class BrowserAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_llm_executes_browser_tools_until_it_finishes(self):
        tool_call = {"name": "navigate_page", "args": {"url": "https://jobright.com"}, "id": "call-1"}
        model = AsyncMock()
        model.ainvoke.side_effect = [
            type("Response", (), {"tool_calls": [tool_call]})(),
            type("Response", (), {"tool_calls": []})(),
        ]
        browser_tool = AsyncMock()
        browser_tool.ainvoke.return_value = "Jobright recommendations are visible."

        async def pass_through_progress(_node, _activity, awaitable, **_kwargs):
            return await awaitable

        original_tools = nodes.tools_by_name.copy()
        nodes.tools_by_name["navigate_page"] = browser_tool
        try:
            with (
                patch.object(
                    nodes,
                    "get_custom_model_with_tools",
                    new_callable=AsyncMock,
                    return_value=model,
                ),
                patch.object(nodes, "await_with_progress", side_effect=pass_through_progress),
                patch.object(nodes, "log_tool_calls"),
                patch.object(nodes, "log_tool_result"),
            ):
                messages = await nodes._run_browser_agent("fetch_jobright_jobs", [])
        finally:
            nodes.tools_by_name.clear()
            nodes.tools_by_name.update(original_tools)

        browser_tool.ainvoke.assert_awaited_once_with({"url": "https://jobright.com"})
        self.assertEqual(model.ainvoke.await_count, 2)
        self.assertIsInstance(messages[-2], nodes.ToolMessage)

    async def test_fetch_node_collects_and_writes_each_job_individually(self):
        jobs = [
            {
                "rank": 1,
                "link": f"https://jobright.com/jobs/info/job-{number}",
                "applyUrl": f"https://jobs.example.com/openings/{number}",
                "title": f"Role {number}",
                "companyName": f"Company {number}",
            }
            for number in range(1, 3)
        ]

        with (
            patch.object(nodes, "recent_workbook_job_summaries", return_value=[]) as recent_workbook_jobs,
            patch.object(
                nodes,
                "collect_jobright_job",
                new_callable=AsyncMock,
                side_effect=[JobrightJob(**job) for job in jobs],
            ) as collect,
            patch.object(nodes, "upsert_job_row", side_effect=[("inserted", 2), ("inserted", 3)]) as upsert,
        ):
            result = await nodes.fetch_jobright_jobs(
                {"jobright_job_count": 2, "excel_file_path": "/tmp/jobs.xlsx"}
            )

        recent_workbook_jobs.assert_called_once_with(Path("/tmp/jobs.xlsx"), limit=20)
        self.assertEqual(collect.await_count, 2)
        self.assertEqual(collect.await_args_list[0].args, (1, 2, []))
        self.assertEqual(
            collect.await_args_list[1].args,
            (2, 2, [{"companyName": "Company 1", "title": "Role 1"}]),
        )
        self.assertEqual(upsert.call_count, 2)
        self.assertEqual(len(result["jobright_jobs"]), 2)
        self.assertIn("individually", result["messages"][0]["content"])

    async def test_fetch_node_keeps_earlier_workbook_writes_when_a_later_job_fails(self):
        job = JobrightJob(
            rank=1,
            link="https://jobright.com/jobs/info/job-1",
            applyUrl="https://jobs.example.com/openings/1",
            title="Role 1",
            companyName="Company 1",
        )

        with (
            patch.object(nodes, "recent_workbook_job_summaries", return_value=[]),
            patch.object(
                nodes,
                "collect_jobright_job",
                new_callable=AsyncMock,
                side_effect=[job, RuntimeError("Jobright timed out")],
            ),
            patch.object(nodes, "upsert_job_row", return_value=("inserted", 2)) as upsert,
        ):
            result = await nodes.fetch_jobright_jobs(
                {"jobright_job_count": 2, "excel_file_path": "/tmp/jobs.xlsx"}
            )

        upsert.assert_called_once()
        self.assertEqual(len(result["jobright_jobs"]), 1)
        self.assertIn("after saving 1 job", result["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
