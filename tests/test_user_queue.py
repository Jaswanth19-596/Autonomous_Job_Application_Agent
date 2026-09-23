import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from node_shared import tool_node
from user_nodes import understand_user_request_node, dispatch_job
from utilities.routing import route_user_request, route_after_job_status_update


class UserQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_appends_deduplicates_and_only_returns_to_caller(self):
        state = {
            "tool_call_origin": "understand_user_request_node",
            "application_queue": ["https://example.com/jobs/1"],
            "messages": [AIMessage(content="", tool_calls=[{
                "name": "store_job_urls",
                "args": {"job_urls": ["https://example.com/jobs/1", "https://example.com/jobs/2"]},
                "id": "store-1",
            }])],
        }
        result = await tool_node(state)
        self.assertEqual(result["application_queue"], ["https://example.com/jobs/1", "https://example.com/jobs/2"])
        self.assertEqual(result["num_jobs_to_process"], 2)
        self.assertNotIn("user_node_decision", result)
        self.assertIsInstance(result["messages"][0], ToolMessage)
        self.assertEqual(result["messages"][0].tool_call_id, "store-1")

    async def test_invalid_urls_do_not_update_queue(self):
        result = await tool_node({
            "tool_call_origin": "understand_user_request_node",
            "messages": [AIMessage(content="", tool_calls=[{
                "name": "store_job_urls", "args": {"job_urls": ["not-a-url"]}, "id": "bad",
            }])],
        })
        self.assertNotIn("application_queue", result)
        self.assertIn("ValueError", result["messages"][0].content)

    async def run_router(self, choice, queue, failure=False):
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(content="Your URLs are stored.")
        with patch("user_nodes.get_custom_model_with_tools", AsyncMock(return_value=model)), patch(
            "user_nodes.use_jev", return_value={"action": {"choice": choice}},
            side_effect=RuntimeError("Unavailable") if failure else None,
        ) as jev:
            result = await understand_user_request_node({
                "messages": [HumanMessage(content="Apply to the stored jobs.")],
                "application_queue": queue,
            })
        return result, jev

    async def test_jev_controls_initial_dispatch_and_preserves_queue(self):
        for choice, destination in [("CHAT", "collect_user_request"), ("APPLY_FOR_JOBS", "dispatch_job")]:
            with self.subTest(choice=choice):
                result, jev = await self.run_router(choice, ["https://example.com/jobs/1"])
                self.assertEqual(route_user_request(result), destination)
                self.assertNotIn("application_queue", result)
                self.assertIn("Your URLs are stored.", jev.call_args.args[0])

    async def test_empty_queue_never_dispatches_or_calls_jev(self):
        result, jev = await self.run_router("APPLY_FOR_JOBS", [])
        self.assertEqual(result["user_node_decision"], "CHAT")
        jev.assert_not_called()

    async def test_jev_failure_does_not_start_or_clear_queue(self):
        result, _ = await self.run_router("APPLY_FOR_JOBS", ["https://example.com/jobs/1"], failure=True)
        self.assertEqual(result["user_node_decision"], "CHAT")
        self.assertNotIn("application_queue", result)

    async def test_batch_continues_without_another_jev_decision(self):
        state = {"application_queue": ["https://example.com/jobs/1", "https://example.com/jobs/2"]}
        with patch("user_nodes.use_jev") as jev:
            first = await dispatch_job(state)
            self.assertEqual(first["job_url"], "https://example.com/jobs/1")
            self.assertEqual(route_after_job_status_update(first), "dispatch_job")
            second = await dispatch_job(first)
            self.assertEqual(second["job_url"], "https://example.com/jobs/2")
            self.assertEqual(route_after_job_status_update(second), "collect_user_request")
            jev.assert_not_called()
