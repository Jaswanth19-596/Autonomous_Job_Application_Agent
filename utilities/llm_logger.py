"""Dedicated logger for capturing all LLM calls, inputs, and outputs."""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    FunctionMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, LLMResult

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE_PATH = LOGS_DIR / "llm_calls.log"

# Ensure logs directory exists
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Dedicated logger for LLM interactions (isolated from standard application logging)
_llm_file_logger = logging.getLogger("llm_calls_file_logger")
_llm_file_logger.setLevel(logging.INFO)
_llm_file_logger.propagate = False

if not _llm_file_logger.handlers:
    _file_handler = logging.FileHandler(LOG_FILE_PATH, mode="a", encoding="utf-8")
    _file_handler.setLevel(logging.INFO)
    _file_formatter = logging.Formatter("%(message)s")
    _file_handler.setFormatter(_file_formatter)
    _llm_file_logger.addHandler(_file_handler)


def _format_message(msg: Any, index: int) -> str:
    """Format an individual input message for clean human-readable log display."""
    if isinstance(msg, SystemMessage):
        role = "SYSTEM"
        content = msg.content
        extra = ""
    elif isinstance(msg, HumanMessage):
        role = "HUMAN / USER"
        content = msg.content
        extra = ""
    elif isinstance(msg, AIMessage):
        role = "AI / ASSISTANT"
        content = msg.content or "(empty text)"
        extra = ""
        if getattr(msg, "tool_calls", None):
            tool_calls_json = json.dumps(msg.tool_calls, indent=2, ensure_ascii=False)
            extra = f"\n  [Prior Tool Calls Requested]:\n{tool_calls_json}"
    elif isinstance(msg, ToolMessage):
        role = f"TOOL RESULT (Name: {getattr(msg, 'name', 'unknown')}, ID: {getattr(msg, 'tool_call_id', 'unknown')})"
        content = msg.content
        extra = ""
    elif isinstance(msg, (FunctionMessage, ChatMessage)):
        role = getattr(msg, "role", type(msg).__name__).upper()
        content = msg.content
        extra = ""
    elif isinstance(msg, dict):
        role = str(msg.get("role", "unknown")).upper()
        content = msg.get("content", str(msg))
        extra = ""
    else:
        role = type(msg).__name__.upper()
        content = getattr(msg, "content", str(msg))
        extra = ""

    lines = [f"[Message {index} | {role}]"]
    if isinstance(content, str):
        lines.append(content)
    else:
        try:
            lines.append(json.dumps(content, indent=2, ensure_ascii=False, default=str))
        except Exception:
            lines.append(str(content))
    if extra:
        lines.append(extra)
    return "\n".join(lines)


class LLMLoggerCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler that records all LLM inputs, outputs, tool calls, and timing to a log file."""

    def __init__(self, log_file: Optional[Path] = None):
        super().__init__()
        self.log_file = log_file or LOG_FILE_PATH
        self._active_runs: dict[str, dict[str, Any]] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Record model start time, invocation metadata, and input messages."""
        run_key = str(run_id)
        start_time = time.time()
        start_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # Extract model name
        model_name = (
            kwargs.get("invocation_params", {}).get("model")
            or (metadata or {}).get("ls_model_name")
            or (serialized or {}).get("name", "UnknownModel")
        )

        formatted_messages: list[str] = []
        msg_idx = 1
        for round_msgs in messages:
            for m in round_msgs:
                formatted_messages.append(_format_message(m, msg_idx))
                msg_idx += 1

        self._active_runs[run_key] = {
            "start_time": start_time,
            "start_dt": start_dt,
            "model_name": model_name,
            "messages": formatted_messages,
            "parent_run_id": str(parent_run_id) if parent_run_id else None,
        }

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Record model response, generated tool calls, duration, and token usage."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        end_time = time.time()
        end_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        if run_data:
            start_dt = run_data["start_dt"]
            model_name = run_data["model_name"]
            formatted_messages = run_data["messages"]
            duration = end_time - run_data["start_time"]
        else:
            start_dt = end_dt
            model_name = "UnknownModel"
            formatted_messages = ["(Input messages not captured)"]
            duration = 0.0

        # Parse outputs
        output_blocks: list[str] = []
        tool_call_blocks: list[str] = []

        for gen_list in response.generations:
            for gen in gen_list:
                if isinstance(gen, ChatGeneration):
                    msg = gen.message
                    if msg.content:
                        output_blocks.append(str(msg.content))
                    if getattr(msg, "tool_calls", None):
                        for tc in msg.tool_calls:
                            tc_name = tc.get("name", "unnamed_tool")
                            tc_args = tc.get("args", {})
                            tc_id = tc.get("id", "no_id")
                            tool_call_blocks.append(
                                f"  • {tc_name} (ID: {tc_id})\n    Args: {json.dumps(tc_args, indent=4, ensure_ascii=False)}"
                            )
                else:
                    if gen.text:
                        output_blocks.append(gen.text)

        output_text = "\n\n".join(output_blocks) if output_blocks else "(No text content generated)"
        tool_calls_text = "\n".join(tool_call_blocks) if tool_call_blocks else "(None)"

        # Parse token usage
        token_usage_str = ""
        if response.llm_output and "token_usage" in response.llm_output:
            tu = response.llm_output["token_usage"]
            prompt_tokens = tu.get("prompt_tokens", "N/A")
            completion_tokens = tu.get("completion_tokens", "N/A")
            total_tokens = tu.get("total_tokens", "N/A")
            token_usage_str = f"\n[Token Usage] Prompt: {prompt_tokens} | Completion: {completion_tokens} | Total: {total_tokens}"
        elif response.llm_output and "usage" in response.llm_output:
            tu = response.llm_output["usage"]
            token_usage_str = f"\n[Token Usage] {json.dumps(tu)}"

        joined_messages = "\n\n".join(formatted_messages)

        # Construct log entry
        log_entry = (
            f"\n{'='*80}\n"
            f"[{start_dt}] LLM CALL START (Run ID: {run_key})\n"
            f"Model: {model_name}\n"
            f"{'-'*80}\n"
            f"INPUT MESSAGES (Count: {len(formatted_messages)}):\n\n"
            f"{joined_messages}\n\n"
            f"{'-'*80}\n"
            f"OUTPUT (Duration: {duration:.2f}s | Finished: {end_dt} | Status: SUCCESS){token_usage_str}\n\n"
            f"[Generated Content]:\n{output_text}\n\n"
            f"[Tool Calls]:\n{tool_calls_text}\n"
            f"{'='*80}\n"
        )

        _llm_file_logger.info(log_entry)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Record model execution failures or timeouts."""
        run_key = str(run_id)
        run_data = self._active_runs.pop(run_key, None)
        end_time = time.time()
        end_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        if run_data:
            start_dt = run_data["start_dt"]
            model_name = run_data["model_name"]
            formatted_messages = run_data["messages"]
            duration = end_time - run_data["start_time"]
        else:
            start_dt = end_dt
            model_name = "UnknownModel"
            formatted_messages = ["(Input messages not captured)"]
            duration = 0.0

        joined_messages = "\n\n".join(formatted_messages)
        log_entry = (
            f"\n{'='*80}\n"
            f"[{start_dt}] LLM CALL FAILED (Run ID: {run_key})\n"
            f"Model: {model_name}\n"
            f"{'-'*80}\n"
            f"INPUT MESSAGES (Count: {len(formatted_messages)}):\n\n"
            f"{joined_messages}\n\n"
            f"{'-'*80}\n"
            f"OUTPUT (Duration: {duration:.2f}s | Finished: {end_dt} | Status: ERROR)\n\n"
            f"[Error Details]:\n{type(error).__name__}: {error}\n"
            f"{'='*80}\n"
        )

        _llm_file_logger.info(log_entry)


# Global singleton instance
llm_logger_handler = LLMLoggerCallbackHandler()
