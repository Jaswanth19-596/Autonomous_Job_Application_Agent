from websockets.client import backoff
from collections import deque
import os
from langgraph.graph import StateGraph, START, END
from nodes import (
    collect_user_request,
    extract_job_details,
    initialize_user_data,
    resolve_application_url,
    perform_login,
    save_job_to_workbook,
    submit_job_application,
    update_job_status,
    is_job_valid,
    tool_node,
    user_request_llm_node,
    build_validation_messages,
    build_verify_job_status_messages,
    verify_job_status,
    shutdown_mcp_tools,
    human_in_the_loop,
    fetch_jobright_jobs,
    prepare_job_application_queue,
    dispath_job,
)
from langgraph.graph.message import MessagesState
from typing import Any, Literal
from prompts import load_prompt
from utilities import route_user_request
from utilities.routing import (
    submit_application_decision,
    route_after_dispatch,
    route_after_jobright_collection,
    route_after_job_status_update,
    route_after_validation,
    route_after_resolving_url,
    route_after_login,
    route_after_verification,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
import aiosqlite
from typing import Annotated
from operator import add
from langgraph.types import Command
from uuid import uuid4
from langgraph.types import RetryPolicy
from langchain_core.exceptions import OutputParserException
from langchain_openai.chat_models.base import OpenAIRateLimitError

def replace_messages(_existing: list[dict], replacement: list[dict]) -> list[dict]:
    """Replace a node-local browser transcript at a workflow boundary."""
    return replacement


class MyState(MessagesState):
    collect_user_request_messages: Annotated[list[dict], add] = []
    submit_job_application_messages: Annotated[list[dict], add] = []
    is_job_valid_messages: Annotated[list[dict], add] = []
    resolve_application_url_messages: Annotated[list[dict], replace_messages] = []
    perform_login_messages: Annotated[list[dict], replace_messages] = []
    verify_job_status_messages: Annotated[list[dict], add] = []
    user_profile: str = ""
    resume_data: str = ""
    resume_file_path: str = ""
    current_state: str = ""

    #user request phase output
    job_url: str = ""
    user_node_decision: Literal["NEEDS_TOOLS", "CHAT", "NEW_JOB_URL", "APPLY_FOR_JOBS", "JOBRIGHT_JOBS", "END"] = "CHAT"
    num_jobs_to_process: int = 0
    job_query_filter: dict[str, Any] | None = None
    application_queue: list[dict] = list()
    current_job: dict = None
    job_details: dict = None
    has_next_job: bool = False

    # Fetch JobRight Jobs
    jobright_jobs: list[dict] = list()
    jobright_job_count: int = 5
    jobright_collection_messages: list = list()
    jobright_collection_job_number: int = 1
    jobright_excluded_jobs: list[dict[str, str]] = list()
    
    pending_jobs_path: str = ""
    applied_jobs_path: str = ""
    is_valid: bool = False
    actual_job_url: str = ""
    authenticated_session: bool = False
    application_status: str = ""
    final_status_message: str = ""
    application_messages: list[dict] = []
    tool_call_origin: str = ""
    next_step: str = ""

    # Human Review fields
    requires_human: bool = False
    human_intervention_reason: Literal["CAPTCHA", "OTHER"] | None = None
    human_intervention_prompt: str | None = ""
    human_intervention_origin: str = ""

    # Application completed fields
    validation_phase : Literal["NEEDS_TOOLS", "ELIGIBLE", "NOT_ELIGIBLE", "END"] = "ELIGIBLE"
    verification_phase : Literal["NEEDS_TOOLS", "APPLIED", "NOT_APPLIED", "END"] = "NOT_APPLIED"
    resolution_phase : Literal["NEEDS_TOOLS", "REQUIRES_LOGIN", "SUCCESS", "FAILED"] = "FAILED"
    login_phase: Literal["NEEDS_TOOLS", "SUCCESS", "FAILED"] = "FAILED"
    login_return_node: Literal["resolve_application_url", "submit_job_application", ""] = ""
    job_application_phase: Literal["NEEDS_TOOLS", "REQUIRES_LOGIN", "APPLIED", "NOT_APPLIED", "END"] = "NOT_APPLIED"

    is_applied: bool = False
    url_applied_on: str = ""

    ready_to_apply: bool = False

graph = StateGraph(MyState)

retry_policy = RetryPolicy(
    initial_interval=0.5,
    backoff_factor = 2.0,
    max_interval = 64,
    max_attempts = 3,
    jitter = True,
    retry_on = [OpenAIRateLimitError, OutputParserException]
)


graph.add_node(initialize_user_data, retry_policy=retry_policy)
graph.add_node(collect_user_request, retry_policy=retry_policy)
graph.add_node(fetch_jobright_jobs, retry_policy=retry_policy)
graph.add_node(extract_job_details, retry_policy=retry_policy)
graph.add_node(save_job_to_workbook, retry_policy=retry_policy)
graph.add_node(is_job_valid, retry_policy=retry_policy)
graph.add_node(resolve_application_url, retry_policy=retry_policy)
graph.add_node(perform_login, retry_policy=retry_policy)
graph.add_node(submit_job_application, retry_policy=retry_policy)
graph.add_node(update_job_status, retry_policy=retry_policy)
graph.add_node(verify_job_status, retry_policy=retry_policy)
graph.add_node("tool_node", tool_node, retry_policy=retry_policy)
graph.add_node(user_request_llm_node, retry_policy=retry_policy)
graph.add_node(build_validation_messages, retry_policy=retry_policy)
graph.add_node(build_verify_job_status_messages, retry_policy=retry_policy)
graph.add_node(human_in_the_loop, retry_policy=retry_policy)
graph.add_node(prepare_job_application_queue, retry_policy=retry_policy)
graph.add_node(dispath_job, retry_policy=retry_policy)

graph.add_edge(START, "initialize_user_data")
graph.add_edge("initialize_user_data", "collect_user_request")
graph.add_edge("collect_user_request", "user_request_llm_node")


graph.add_conditional_edges(
    "user_request_llm_node",
    route_user_request,
    {
        "tool_node": "tool_node",
        "collect_user_request": "collect_user_request",
        "extract_job_details": "extract_job_details",
        "fetch_jobright_jobs": "fetch_jobright_jobs",
        "build_validation_messages": "build_validation_messages",
        "prepare_job_application_queue": "prepare_job_application_queue",
        "END": END,
    },
)

graph.add_conditional_edges(
    "perform_login",
    route_after_login,
    {
        "tool_node": "tool_node",
        "resolve_application_url": "resolve_application_url",
        "submit_job_application": "submit_job_application",
        "human_in_the_loop": "human_in_the_loop",
    },
)
graph.add_edge("prepare_job_application_queue", "dispath_job")
graph.add_conditional_edges(
    "dispath_job",
    route_after_dispatch,
    {
        "extract_job_details": "extract_job_details",
        "collect_user_request": "collect_user_request",
    },
)
graph.add_conditional_edges(
    "fetch_jobright_jobs",
    route_after_jobright_collection,
    {
        "tool_node": "tool_node",
        "fetch_jobright_jobs": "fetch_jobright_jobs",
        "collect_user_request": "collect_user_request",
    },
)
graph.add_edge("build_validation_messages", "is_job_valid")
graph.add_edge("extract_job_details", "save_job_to_workbook")
graph.add_edge("save_job_to_workbook", "build_validation_messages")
graph.add_conditional_edges(
    "update_job_status",
    route_after_job_status_update,
    {
        "dispath_job": "dispath_job",
        "collect_user_request": "collect_user_request",
    },
)

graph.add_conditional_edges(
    "is_job_valid",
    route_after_validation,
    {
        "tool_node": "tool_node",
        "resolve_application_url": "resolve_application_url",
        "update_job_status": "update_job_status",
    },
)

graph.add_conditional_edges(
    "resolve_application_url",
    route_after_resolving_url,
    {
        "tool_node": "tool_node",
        "perform_login": "perform_login",
        "submit_job_application": "submit_job_application",
        "update_job_status": "update_job_status",
        "human_in_the_loop": "human_in_the_loop"
    },
)

graph.add_conditional_edges(
    "submit_job_application",
    submit_application_decision,
    {
        "tool_node": "tool_node",
        "perform_login": "perform_login",
        "build_verify_job_status_messages": "build_verify_job_status_messages",
        "human_in_the_loop": "human_in_the_loop",
        "update_job_status": "update_job_status",
    },
)

graph.add_edge("build_verify_job_status_messages", "verify_job_status")


graph.add_conditional_edges(
    "verify_job_status",
    route_after_verification,
    {
        "tool_node": "tool_node",
        "update_job_status": "update_job_status",
    },
)

graph.add_conditional_edges(
    "tool_node",
    lambda state: state["tool_call_origin"],
    {
        "user_request_llm_node": "user_request_llm_node",
        "is_job_valid": "is_job_valid",
        "resolve_application_url": "resolve_application_url",
        "perform_login": "perform_login",
        "submit_job_application": "submit_job_application",
        "verify_job_status": "verify_job_status",
        "fetch_jobright_jobs": "fetch_jobright_jobs",
    },
)

graph.add_conditional_edges("human_in_the_loop", lambda state: state["next_step"], {
    "submit_job_application": "submit_job_application",
    "resolve_application_url": "resolve_application_url",
    "perform_login": "perform_login",
})

# Wire the new node: only when human is required, route to submit_application_decision
# graph.add_conditional_edges(
#     "submit_job_application",
#     submit_application_decision,
#     {
#         "human_intervention": END,
#         "update_job_status": "update_job_status",
#         "END": END,
#     },
# )



async def main():
    """Run the graph from the command line.

    User data is loaded automatically by the initialize_user_data node,
    so we only need to provide the system prompt message.
    """

    conn = await aiosqlite.connect("my_database.db")
    checkpointer = AsyncSqliteSaver(
        conn,
        serde=JsonPlusSerializer(
            allowed_msgpack_modules=[("models.sub_schemas", "JobQueryFilter")]
        ),
    )
    workflow = graph.compile(checkpointer=checkpointer)


    messages = [
        {"role": "system", "content": load_prompt("chat_with_user/system.txt")},
    ]

    # A CLI launch begins a new application run. Reusing the same fixed thread
    # ID silently resumes an old, possibly partial tool conversation.
    config = {
        "configurable": {
            "thread_id": os.getenv("JOB_AGENT_THREAD_ID", str(uuid4()))
        }
    }

    try:
        result = await workflow.ainvoke({"messages": messages, "requires_human": False}, config = config)

        while "__interrupt__" in result:
            interrupt_data = result["__interrupt__"][0].value
            user_input = input(interrupt_data["prompt"])
            result = await workflow.ainvoke(Command(resume = user_input), config = config)
    finally:
        await shutdown_mcp_tools()
        await conn.close()


import asyncio
if __name__ == "__main__":
    asyncio.run(main())
