import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utilities.application_timing import finish_application_timing, start_application_timing


class ApplicationTimingTests(unittest.TestCase):
    def test_duration_and_job_are_persisted_and_timer_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'timings.jsonl'
            with patch('utilities.application_timing.TIMING_LOG_PATH', path), \
                 patch('utilities.application_timing.time.time', side_effect=[100, 112.3456, 200]), \
                 patch('utilities.application_timing.log_workflow_event'):
                state = {**start_application_timing(), 'job_url': 'https://example.com/job',
                         'job_details': {'id': '42', 'title': 'Engineer', 'companyName': 'Example'}}
                result = finish_application_timing(state, 'Applied')
                record = json.loads(path.read_text())
                self.assertEqual(record['elapsed_seconds'], 12.346)
                self.assertEqual(record['job_id'], '42')
                self.assertEqual(record['outcome'], 'Applied')
                self.assertIsNone(result['application_started_at'])
                self.assertEqual(start_application_timing()['application_started_at'], 200)

    def test_rejected_job_does_not_inherit_previous_job_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'timings.jsonl'
            with patch('utilities.application_timing.TIMING_LOG_PATH', path), \
                 patch('utilities.application_timing.log_workflow_event'):
                finish_application_timing({'application_started_at': 100,
                    'validation_phase': 'NOT_ELIGIBLE', 'job_url': 'current',
                    'job_details': {'id': 'previous'}}, 'Not Applied')
                record = json.loads(path.read_text())
                self.assertEqual(record['job_url'], 'current')
                self.assertIsNone(record['job_id'])

    def test_legacy_checkpoint_has_no_invented_duration(self):
        self.assertEqual(finish_application_timing({}, 'Applied'), {})

    def test_timing_is_saved_on_matching_excel_row(self):
        from openpyxl import Workbook, load_workbook

        with tempfile.TemporaryDirectory() as directory:
            workbook_path = Path(directory) / "applied_jobs.xlsx"
            book = Workbook()
            book.active.append(["id", "application_status"])
            book.active.append(["42", "Applied"])
            book.active.append(["other", "Not Applied"])
            book.save(workbook_path)
            book.close()
            with patch('utilities.application_timing.TIMING_LOG_PATH', Path(directory) / 'timings.jsonl'), \
                 patch('utilities.application_timing.time.time', return_value=1700000012.3456), \
                 patch('utilities.application_timing.log_workflow_event'):
                finish_application_timing({"application_started_at": 1700000000,
                    "job_details": {"id": "42"}}, "Applied", workbook_path=workbook_path)
            book = load_workbook(workbook_path)
            try:
                rows = list(book.active.values)
                saved = dict(zip(rows[0], rows[1]))
                other = dict(zip(rows[0], rows[2]))
                self.assertEqual(saved["application_elapsed_seconds"], 12.346)
                self.assertEqual(saved["application_status"], "Applied")
                self.assertTrue(saved["application_started_at"].endswith("+00:00"))
                self.assertIsNone(other["application_elapsed_seconds"])
            finally:
                book.close()
