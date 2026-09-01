import unittest
from pathlib import Path
import uuid
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from utilities.llm_logger import LLMLoggerCallbackHandler, LOG_FILE_PATH


class TestLLMLogger(unittest.TestCase):
    def setUp(self):
        self.handler = LLMLoggerCallbackHandler()

    def test_logs_successful_llm_interaction(self):
        run_id = uuid.uuid4()
        messages = [
            [
                SystemMessage(content="You are an assistant."),
                HumanMessage(content="Hello!"),
            ]
        ]
        self.handler.on_chat_model_start(
            serialized={"name": "test_model"},
            messages=messages,
            run_id=run_id,
            invocation_params={"model": "test-chat-model"},
        )

        ai_msg = AIMessage(
            content="Hello! How can I help you today?",
            tool_calls=[
                {
                    "name": "get_jobs",
                    "args": {"query": "engineer"},
                    "id": "call_123",
                }
            ],
        )
        chat_gen = ChatGeneration(message=ai_msg)
        response = LLMResult(
            generations=[[chat_gen]],
            llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}},
        )

        self.handler.on_llm_end(response, run_id=run_id)

        self.assertTrue(LOG_FILE_PATH.exists())
        with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("LLM CALL START", content)
        self.assertIn("You are an assistant.", content)
        self.assertIn("Hello!", content)
        self.assertIn("Hello! How can I help you today?", content)
        self.assertIn("get_jobs", content)

    def test_logs_failed_llm_interaction(self):
        run_id = uuid.uuid4()
        messages = [
            [
                HumanMessage(content="Please fetch jobs"),
            ]
        ]
        self.handler.on_chat_model_start(
            serialized={"name": "test_model"},
            messages=messages,
            run_id=run_id,
            invocation_params={"model": "test-chat-model"},
        )

        self.handler.on_llm_error(TimeoutError("Connection timed out"), run_id=run_id)

        with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("LLM CALL FAILED", content)
        self.assertIn("Connection timed out", content)


if __name__ == "__main__":
    unittest.main()
