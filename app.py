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
    understand_user_request_node,
    verify_job_status,
    shutdown_mcp_tools,
    human_in_the_loop,
    dispatch_job,
    customize_resume
)
from models.schemas import JobDetailsModel

from langgraph.graph.message import MessagesState
from typing import Any, Literal
from prompts import load_prompt
from utilities import route_user_request
from utilities.routing import (
    submit_application_decision,
    route_after_dispatch,
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
    submit_job_application_messages: Annotated[list[dict], add] = []
    is_job_valid_messages: Annotated[list[dict], add] = []
    resolve_application_url_messages: Annotated[list[dict], add] = []
    perform_login_messages: Annotated[list[dict], add] = []
    verify_job_status_messages: Annotated[list[dict], add] = []

    user_profile: dict = {}
    resume_data: str = ""
    resume_file_path: str = ""
    current_state: str = ""

    #user request phase output
    job_url: str = ""
    user_node_decision: Literal["NEEDS_TOOLS", "CHAT", "APPLY_FOR_JOBS", "END"] = "CHAT"
    num_jobs_to_process: int = 0

    application_queue: list[str] = list()
    completed_jobs: Annotated[list[str], add] = []
    current_job: dict = None
    has_next_job: bool = False

    #customize resume phase
    job_details: JobDetailsModel = None
    customized_resume_path: str = ""
    customized_resume: str = ""
    projects: str = ""
    experiences: str = ""
    reference_resume_path: str = ""
    
    pending_jobs_path: str = ""
    applied_jobs_path: str = ""
    is_valid: bool = False
    actual_job_url: str = ""
    is_authenticated: bool = False
    application_status: str = ""
    application_messages: list[dict] = []
    tool_call_origin: str = ""
    next_step: str = ""

    # Human Review fields
    requires_human: bool = False
    human_intervention_reason: Literal["CAPTCHA", "QUESTION", "SECURITY", "OTHER"] | None = None
    question_scope: Literal["global", "employer", "job"] = "job"
    application_recovery_attempts: int = 0
    human_intervention_prompt: str | None = ""
    human_intervention_origin: str = ""
    human_response: str = ""

    # Application completed fields
    validation_phase : Literal["NEEDS_TOOLS", "ELIGIBLE", "NOT_ELIGIBLE", "END"] = "ELIGIBLE"
    verification_phase : Literal["NEEDS_TOOLS", "APPLIED", "NOT_APPLIED", "END"] = "NOT_APPLIED"
    resolution_phase : Literal["NEEDS_TOOLS", "REQUIRES_LOGIN", "SUCCESS", "FAILED"] = "FAILED"
    login_phase: Literal["NEEDS_TOOLS", "SUCCESS", "FAILED", "REQUIRES_HUMAN"] = "FAILED"
    login_return_node: Literal["resolve_application_url", "submit_job_application"] = None
    job_application_phase: Literal["NEEDS_TOOLS", "CONTINUE", "REQUIRES_LOGIN", "APPLIED", "NOT_APPLIED", "END"] = "NOT_APPLIED"

    is_applied: bool = False
    url_applied_on: str = ""
    final_status_message: str = ""

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
graph.add_node(extract_job_details, retry_policy=retry_policy)
graph.add_node(save_job_to_workbook, retry_policy=retry_policy)
graph.add_node(is_job_valid, retry_policy=retry_policy)
graph.add_node(resolve_application_url, retry_policy=retry_policy)
graph.add_node(perform_login, retry_policy=retry_policy)
graph.add_node(submit_job_application, retry_policy=retry_policy)
graph.add_node(update_job_status, retry_policy=retry_policy)
graph.add_node(verify_job_status, retry_policy=retry_policy)
graph.add_node("tool_node", tool_node, retry_policy=retry_policy)
graph.add_node(understand_user_request_node, retry_policy=retry_policy)
graph.add_node(human_in_the_loop, retry_policy=retry_policy)
graph.add_node(dispatch_job, retry_policy=retry_policy)
graph.add_node(customize_resume, retry_policy=retry_policy)
graph.add_edge(START, "initialize_user_data")
graph.add_edge("initialize_user_data", "collect_user_request")
graph.add_edge("collect_user_request", "understand_user_request_node")


graph.add_conditional_edges(
    "understand_user_request_node",
    route_user_request
)

graph.add_conditional_edges(
    "perform_login",
    route_after_login
)
graph.add_conditional_edges(
    "dispatch_job",
    route_after_dispatch
)
graph.add_conditional_edges(
    "is_job_valid",
    route_after_validation
)

graph.add_edge("extract_job_details", "save_job_to_workbook")
graph.add_edge("save_job_to_workbook", "customize_resume")
graph.add_edge("customize_resume" , "resolve_application_url")
graph.add_conditional_edges(
    "resolve_application_url",
    route_after_resolving_url
)
graph.add_conditional_edges(
    "update_job_status",
    route_after_job_status_update
)


graph.add_conditional_edges(
    "submit_job_application",
    submit_application_decision
)



graph.add_conditional_edges(    
    "verify_job_status",
    route_after_verification
)

graph.add_conditional_edges(
    "tool_node",
    lambda state: state["tool_call_origin"],
    {
        "understand_user_request_node": "understand_user_request_node",
        "is_job_valid": "is_job_valid",
        "resolve_application_url": "resolve_application_url",
        "perform_login": "perform_login",
        "submit_job_application": "submit_job_application",
        "verify_job_status": "verify_job_status",
    },
)

graph.add_conditional_edges("human_in_the_loop", lambda state: state["next_step"], {
    "submit_job_application": "submit_job_application",
    "resolve_application_url": "resolve_application_url",
    "perform_login": "perform_login",
    "update_job_status": "update_job_status",
})



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

    config = {
        "configurable": {
            "thread_id": os.getenv("JOB_AGENT_THREAD_ID", str(uuid4()))
        }
    }

    try:
        result = await workflow.ainvoke({}, config = config)

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
