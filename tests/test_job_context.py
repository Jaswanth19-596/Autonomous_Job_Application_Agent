import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpyxl import Workbook

from utilities import job_context


def write_workbook(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


class JobContextToolTests(unittest.TestCase):
    def test_reads_the_requested_pending_or_invalid_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "pending_jobs.xlsx"
            invalid = root / "invalid_jobs.xlsx"
            write_workbook(pending, ["url", "fetched_at"], [["https://example.com/pending", "today"]])
            write_workbook(invalid, ["url", "reason"], [["https://example.com/invalid", "Missing experience"]])

            with patch.object(job_context, "_WORKBOOKS", {"pending": pending, "invalid": invalid}):
                pending_result = job_context.get_pending_or_invalid_jobs.invoke({"job_type": "pending"})
                invalid_result = job_context.get_pending_or_invalid_jobs.invoke({"job_type": "invalid"})
                all_result = job_context.get_pending_or_invalid_jobs.invoke({"job_type": "all"})

        self.assertEqual(pending_result["jobs"], [{"job_source": "pending", "url": "https://example.com/pending", "fetched_at": "today"}])
        self.assertEqual(invalid_result["jobs"], [{"job_source": "invalid", "url": "https://example.com/invalid", "reason": "Missing experience"}])
        self.assertEqual(all_result["total_matching"], 2)

    def test_searches_and_limits_context_records(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pending = root / "pending_jobs.xlsx"
            invalid = root / "invalid_jobs.xlsx"
            write_workbook(pending, ["url", "fetched_at"], [["https://example.com/pending", "today"]])
            write_workbook(invalid, ["url", "reason"], [["https://example.com/invalid", "Missing experience"]])

            with patch.object(job_context, "_WORKBOOKS", {"pending": pending, "invalid": invalid}):
                result = job_context.get_pending_or_invalid_jobs.invoke(
                    {"job_type": "all", "query": "experience", "fields": ["reason"]}
                )

        self.assertEqual(result["total_matching"], 1)
        self.assertEqual(result["jobs"], [{"job_source": "invalid", "reason": "Missing experience"}])


if __name__ == "__main__":
    unittest.main()
