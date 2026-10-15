"""CLI recovery resumes the failed checkpoint without repeating the input."""
import unittest
from unittest.mock import AsyncMock, patch

from app import invoke_with_drive_recovery
from workbook.drive import DriveStorageError


class DriveRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_resumes_checkpoint(self):
        workflow = AsyncMock()
        workflow.ainvoke.side_effect = [DriveStorageError("offline"), {"done": True}]
        config = {"configurable": {"thread_id": "same-thread"}}
        with patch("builtins.input", return_value=""), patch("builtins.print"):
            result = await invoke_with_drive_recovery(workflow, {"start": True}, config)
        self.assertEqual(result, {"done": True})
        self.assertEqual(workflow.ainvoke.call_args_list[1].args, (None,))
        self.assertEqual(workflow.ainvoke.call_args_list[1].kwargs["config"], config)

    async def test_quit_does_not_retry_or_raise(self):
        workflow = AsyncMock()
        workflow.ainvoke.side_effect = DriveStorageError("offline")
        with patch("builtins.input", return_value="quit"), patch("builtins.print"):
            self.assertEqual(await invoke_with_drive_recovery(workflow, {}, {}), {})
        self.assertEqual(workflow.ainvoke.call_count, 1)
