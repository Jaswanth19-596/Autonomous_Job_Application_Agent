"""Shared model, tool, and MCP infrastructure for workflow nodes."""

import asyncio
import logging
import json
import os
from urllib.parse import urlparse

from langchain.messages import ToolMessage
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

from mcp_connection.mcp_manager import MCPManager
from models.schemas import (
    ApplicationResult,
    ApplicationUrlResolutionResult,
    ApplicationVerificationResult,
    AuthenticationResult,
    JobDetailsModel,
    UserRequestDecision,
    ValidJobOrNotDecision,
)
from utilities import (
    await_with_progress,
    get_applied_jobs,
    get_pending_or_invalid_jobs,
    log_node_stage,
    log_tool_calls,
)
from utilities.application_answers import create_application_answer_tool
from utilities.form_fill import verified_fill
from utilities.opencode import OPENCODE_USER_AGENT, opencode_session_headers
from utilities.profile_memory import applicable_profile, load_profile


load_dotenv()


model = ChatOpenAI(
    model="muse-spark-1.3-contributor",
    api_key=os.getenv("OPENCODE_API_KEY"),
    base_url="https://opencode.ai/zen/go/v1",
    default_headers={"User-Agent": OPENCODE_USER_AGENT},
    use_responses_api=True,
    timeout=120.0,
    max_retries=2,
)

application_answer_model = ChatOpenAI(
    model="gpt-5.6-luna",
    api_key=os.getenv("OPENCODE_API_KEY"),
    base_url="https://opencode.ai/zen/go/v1",
    default_headers={"User-Agent": OPENCODE_USER_AGENT},
    use_responses_api=True,
    timeout=120.0,
    max_retries=2,
)

mcp_manager = MCPManager()


def visible_response_text(content: object) -> str:
    """Return only user-visible text from Chat Completions or Responses content."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    text_blocks = []
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            text = block.get("text")
        else:
            block_type = getattr(block, "type", None)
            text = getattr(block, "text", None)
        if block_type in {"text", "output_text"} and isinstance(text, str):
            text_blocks.append(text)
    return "\n".join(text_blocks)


async def get_custom_model_with_tools(
    tool_list,
    config: RunnableConfig | None = None,
    application_answer_source: dict | None = None,
):
    """Load requested MCP tools before binding them to the model."""
    headers = opencode_session_headers(config)
    mcp_servers = [tool_name for tool_name in tool_list if tool_name in {"chrome", "gmail"}]
    mcp_tools = await mcp_manager.start(mcp_servers) if mcp_servers else []

    for tool in mcp_tools:
        tools_by_name[tool.name] = tool

    tools = [*mcp_tools]
    if "custom" in tool_list:
        tools.extend((get_applied_jobs, get_pending_or_invalid_jobs, store_job_urls))
    if "application_answers" in tool_list:
        if application_answer_source is None:
            raise ValueError("Application-answer tools require workflow source context")
        tools.append(create_application_answer_tool(
            application_answer_model, application_answer_source
        ))
    return model.bind_tools(tools=tools, extra_headers=headers)

def _with_go_structured_output(schema, config: RunnableConfig | None = None):
    """Use Go-compatible tool output without forcing an unsupported named tool."""
    return model.with_structured_output(
        schema,
        method="function_calling",
        tool_choice="auto",
        # # Muse Spark's Responses API accepts only automatic tool selection.
        # # The structured-output prompt still requires the returned schema tool.
        # tool_choice="required",
        extra_headers=opencode_session_headers(config),
    )


def get_custom_structured_model(model_type, config: RunnableConfig | None = None):
    if model_type == "job_application_model":
        return _with_go_structured_output(ApplicationResult, config)
    elif model_type == "user_request_model":
        return _with_go_structured_output(UserRequestDecision, config)
    elif model_type == "job_details_model":
        return _with_go_structured_output(JobDetailsModel, config)
    elif model_type == "job_validation_model":
        return _with_go_structured_output(ValidJobOrNotDecision, config)
    elif model_type == "application_url_resolution_model":
        return _with_go_structured_output(ApplicationUrlResolutionResult, config)
    elif model_type == "authentication_model":
        return _with_go_structured_output(AuthenticationResult, config)
    elif model_type == "application_verification_model":
        return _with_go_structured_output(ApplicationVerificationResult, config)
    else:
        return None
    



@tool
def store_job_urls(job_urls: list[str]) -> list[str]:
    """Store exact user-selected job URLs without starting applications.

    Pass newly supplied URLs or URLs resolved by lookup tools. Do not invent
    URLs or re-add jobs from completed requests. Existing queued URLs are kept.
    """
    if not job_urls or any(not is_http_job_url(url) for url in job_urls):
        raise ValueError("Provide a non-empty list of exact HTTP(S) job URLs.")
    return list(dict.fromkeys(url.strip() for url in job_urls))



tools_by_name = {
    "store_job_urls": store_job_urls,
    "get_applied_jobs": get_applied_jobs,
    "get_pending_or_invalid_jobs": get_pending_or_invalid_jobs,
}


def application_answer_source(state, profile=None):
    """Return the complete, scoped evidence set for questionnaire drafting."""
    scoped_profile = applicable_profile(profile or load_profile(), state)
    customized_resume = state.get("customized_resume", "")
    if not customized_resume:
        raise ValueError("Customized resume text is unavailable for questionnaire drafting")
    return {
        "job_details": state.get("job_details"),
        "user_profile": scoped_profile,
        "customized_resume": customized_resume,
        "application_answers": scoped_profile.get("application_answers", {}),
        "projects": state.get("projects"),
        "experiences": state.get("experiences", ""),
    }



logger = logging.getLogger(__name__)


async def shutdown_mcp_tools():
    """Stop the manager-owned persistent MCP sessions after a workflow run."""
    await mcp_manager.stop()


def is_http_job_url(value: str) -> bool:
    """Return whether a routing decision contains a usable job-posting URL."""
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

async def tool_node(state):
    origin_node = state.get("tool_call_origin", "unknown_node")
    node_name = f"{origin_node} → tool_node"
    log_node_stage(node_name, "Started — executing tools")

    message_keys = {
        "submit_job_application": "submit_job_application_messages",
        "is_job_valid": "is_job_valid_messages",
        "resolve_application_url": "resolve_application_url_messages",
        "perform_login": "perform_login_messages",
        "verify_job_status": "verify_job_status_messages",
    }
    message_key = message_keys.get(state["tool_call_origin"], "messages")
    messages = state[message_key]

    last_message = messages[-1]

    tool_calls = last_message.tool_calls
    log_tool_calls(origin_node, tool_calls)

    tool_messages = []
    queue_update = {}
    for tool_call in tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_call_id = tool_call["id"]

        if tool_name == "generate_application_answer":
            tool = create_application_answer_tool(
                application_answer_model, application_answer_source(state)
            )
        else:
            tool = tools_by_name.get(tool_name)
        if tool is None:
            error = f"Unknown tool requested: {tool_name}"
            logger.warning(error)
            tool_messages.append(
                ToolMessage(content=error, tool_call_id=tool_call_id, name=tool_name)
            )
            continue
        try:
            if tool_name == "store_job_urls" and origin_node != "understand_user_request_node":
                raise ValueError("Job URLs can only be stored by the user-request node.")
            tool_result = await await_with_progress(
                node_name,
                f"Running tool: {tool_name}",
                asyncio.wait_for(
                    verified_fill(tool_name, tool_args, tools_by_name)
                    if tool_name in {"fill", "fill_form"}
                    else tool.ainvoke(tool_args),
                    timeout=60,
                ),
                show_progress=False,
            )
            if tool_name == "store_job_urls":
                queue = list(dict.fromkeys([
                    *queue_update.get("application_queue", state.get("application_queue", [])),
                    *tool_result,
                ]))
                queue_update = {
                    "application_queue": queue,
                    "num_jobs_to_process": len(queue),
                }
                tool_result = json.dumps({
                    "stored_job_urls": tool_result,
                    "application_queue": queue,
                    "message": "URLs stored only; applications have not started.",
                })
            tool_messages.append(
            ToolMessage(
                content=tool_result,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        )
        except asyncio.TimeoutError:
            tool_result = {"error": f"Tool {tool_name} timed out after 60 seconds"}
            logger.exception("Tool %s timed out", tool_name)
            tool_messages.append(
                ToolMessage(
                    content=tool_result,
                    tool_call_id=tool_call_id,
                    name=tool_name,
                )
            )
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            tool_result = {"error": f"{type(exc).__name__}: {exc}"}
            tool_messages.append(
                ToolMessage(
                    content=tool_result,
                    tool_call_id=tool_call_id,
                    name=tool_name,
                )
            )

    if origin_node == "resolve_application_url":
        return {"resolve_application_url_messages": tool_messages}
    elif origin_node == "perform_login":
        return {"perform_login_messages": tool_messages}
    elif origin_node == "is_job_valid":
        return {"is_job_valid_messages": tool_messages}
    elif origin_node == "submit_job_application":
        return {"submit_job_application_messages": tool_messages}
    elif origin_node == "verify_job_status":
        return {"verify_job_status_messages": tool_messages}
    return {"messages": tool_messages, **queue_update}
