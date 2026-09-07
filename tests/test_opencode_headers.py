import asyncio
import json
import unittest
from collections import Counter
from unittest.mock import patch

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

import nodes
from utilities.opencode import OPENCODE_USER_AGENT, opencode_session_headers


STRUCTURED_MODELS = (
    "job_application_model",
    "user_request_model",
    "job_details_model",
    "job_validation_model",
    "application_url_resolution_model",
    "authentication_model",
    "application_verification_model",
    "jobright_job_model",
)


def response_for(request):
    return httpx.Response(
        200,
        json={
            "id": "resp_test",
            "object": "response",
            "created_at": 0,
            "model": "muse-spark-1.3-contributor",
            "status": "completed",
            "output": [{
                "type": "message",
                "id": "msg_test",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "OK", "annotations": []}],
            }],
        },
        request=request,
    )


class OpenCodeHeadersTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_model_paths_isolate_conversations_and_preserve_resume(self):
        requests = []

        async def capture(request):
            await asyncio.sleep(0)  # Interleave requests from both conversations.
            requests.append(request)
            return response_for(request)

        async def infer(state):
            tool_model = await nodes.get_custom_model_with_tools(["custom"])
            await tool_model.ainvoke(state["messages"])
            for model_type in STRUCTURED_MODELS:
                await nodes.get_custom_structured_model(model_type).ainvoke(state["messages"])
            return {}

        def pause(state):
            interrupt("Continue")
            return {}

        builder = StateGraph(MessagesState)
        builder.add_node("before", infer)
        builder.add_node("pause", pause)
        builder.add_node("after", infer)
        builder.add_edge(START, "before")
        builder.add_edge("before", "pause")
        builder.add_edge("pause", "after")
        builder.add_edge("after", END)
        saver = InMemorySaver()
        graph = builder.compile(checkpointer=saver)

        async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as client:
            # Keep the actual application's model configuration and SDK defaults.
            model = nodes.ChatOpenAI(
                model=nodes.model.model_name,
                api_key="test-only",
                base_url=nodes.model.openai_api_base,
                use_responses_api=nodes.model.use_responses_api,
                default_headers=nodes.model.default_headers,
                http_async_client=client,
                max_retries=0,
            )
            with patch.object(nodes, "model", model):
                configs = [{"configurable": {"thread_id": name}} for name in ("thread-a", "thread-b")]
                await asyncio.gather(*(
                    graph.ainvoke({"messages": [("user", config["configurable"]["thread_id"])]}, config)
                    for config in configs
                ))
                # Recompile to model a new graph instance resuming saved conversations.
                resumed = builder.compile(checkpointer=saver)
                await asyncio.gather(*(
                    resumed.ainvoke(Command(resume="continue"), config) for config in configs
                ))

        self.assertEqual(len(requests), 36)
        for request in requests:
            body = json.loads(request.content)
            conversation = body["input"][0]["content"]
            self.assertEqual(request.headers["x-opencode-session"], conversation)
            self.assertEqual(request.headers["user-agent"], OPENCODE_USER_AGENT)
            self.assertEqual(request.url.path, "/zen/go/v1/responses")
            self.assertNotIn("extra_headers", body)
        self.assertEqual(
            Counter(request.headers["x-opencode-session"] for request in requests),
            {"thread-a": 18, "thread-b": 18},
        )
        self.assertNotIn("x-opencode-session", model.default_headers)

    async def test_sdk_retry_preserves_headers(self):
        requests = []

        def capture(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(500, json={"error": {"message": "retry"}}, request=request)
            return response_for(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as client:
            model = nodes.ChatOpenAI(
                model=nodes.model.model_name,
                api_key="test-only",
                base_url=nodes.model.openai_api_base,
                use_responses_api=True,
                default_headers=nodes.model.default_headers,
                http_async_client=client,
                max_retries=1,
            )
            with patch.object(nodes, "model", model):
                bound = await nodes.get_custom_model_with_tools(
                    ["custom"], {"configurable": {"thread_id": "retry-session"}}
                )
                await bound.ainvoke("hello")

        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(request.headers["x-opencode-session"], "retry-session")
            self.assertEqual(request.headers["user-agent"], OPENCODE_USER_AGENT)

    async def test_missing_conversation_fails_before_loading_tools_or_sending(self):
        with patch.object(nodes.mcp_manager, "start") as start:
            with self.assertRaisesRegex(ValueError, "thread_id"):
                await nodes.get_custom_model_with_tools(["chrome"])
            start.assert_not_called()
        with self.assertRaisesRegex(ValueError, "thread_id"):
            nodes.get_custom_structured_model("user_request_model")

    def test_invalid_header_values_are_rejected(self):
        for thread_id in (None, "", " ", "new\nheader", "new\rheader", "café"):
            with self.subTest(thread_id=thread_id), self.assertRaises(ValueError):
                opencode_session_headers({"configurable": {"thread_id": thread_id}})


if __name__ == "__main__":
    unittest.main()
