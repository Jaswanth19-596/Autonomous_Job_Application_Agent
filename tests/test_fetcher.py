import tempfile
import unittest
from pathlib import Path
import pandas as pd

from jobs.fetcher import save_jobs_to_excel, PENDING_JOB_COLUMNS, _extract_job_url


class FetcherPendingJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.excel_path = Path(self.temp_dir.name) / "pending_jobs.xlsx"

    def test_extract_job_url_supports_multiple_keys(self):
        self.assertEqual(
            _extract_job_url({"link": "https://www.linkedin.com/jobs/view/12345"}),
            "https://www.linkedin.com/jobs/view/12345",
        )
        self.assertEqual(
            _extract_job_url({"applyUrl": "https://boards.greenhouse.io/company/jobs/67890"}),
            "https://boards.greenhouse.io/company/jobs/67890",
        )
        self.assertEqual(
            _extract_job_url({"url": "https://jobright.ai/jobs/info/abcde"}),
            "https://jobright.ai/jobs/info/abcde",
        )
        self.assertEqual(
            _extract_job_url("https://example.com/job/1"),
            "https://example.com/job/1",
        )
        self.assertEqual(_extract_job_url({"link": "null", "url": ""}), "")

    def test_save_jobs_creates_pending_excel_with_exact_columns(self):
        jobs = [
            {
                "id": "1",
                "link": "https://www.linkedin.com/jobs/view/1001",
                "title": "AI Engineer",
                "companyName": "TechCo",
                "salary": "$150k",
            },
            {
                "id": "2",
                "applyUrl": "https://www.linkedin.com/jobs/view/1002",
                "title": "Software Engineer",
                "location": "Remote",
            },
        ]
        df = save_jobs_to_excel(jobs, file_path=self.excel_path)
        self.assertEqual(list(df.columns), list(PENDING_JOB_COLUMNS))
        self.assertEqual(list(df.columns), ["url", "fetched_at"])
        self.assertEqual(len(df), 2)

        saved = pd.read_excel(self.excel_path)
        self.assertEqual(list(saved.columns), ["url", "fetched_at"])
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(saved["url"].str.contains("linkedin.com")))
        self.assertTrue(all(saved["fetched_at"].str.len() > 0))

    def test_save_jobs_deduplicates_and_preserves_existing(self):
        initial_jobs = [
            {"link": "https://www.linkedin.com/jobs/view/1001?trk=ref1"},
            {"link": "https://www.linkedin.com/jobs/view/1002"},
        ]
        save_jobs_to_excel(initial_jobs, file_path=self.excel_path)

        # Append with 1 duplicate (different tracking param) and 1 new job
        new_jobs = [
            {"link": "https://www.linkedin.com/jobs/view/1001?trk=ref2"},
            {"link": "https://www.linkedin.com/jobs/view/1003"},
        ]
        combined = save_jobs_to_excel(new_jobs, file_path=self.excel_path)
        self.assertEqual(len(combined), 3)

        saved = pd.read_excel(self.excel_path)
        self.assertEqual(len(saved), 3)
        self.assertEqual(list(saved.columns), ["url", "fetched_at"])


if __name__ == "__main__":
    unittest.main()
