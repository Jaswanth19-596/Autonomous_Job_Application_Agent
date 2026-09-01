from models.schemas import JobQueryFilter
from collections import deque
from langgraph.graph import StateGraph, START, END
from nodes import (
    authenticate_with_job_site,
    collect_user_request,
    extract_job_details,
    initialize_user_data,
    resolve_application_url,
    save_job_to_workbook,
    submit_job_application,
    update_job_status,
    validate_job_listing,
    tool_node,
    user_request_llm_node,
    intermediate_node_for_submit_job_application,
    build_validation_messages,
    build_application_url_messages,
    build_authenticate_with_job_site_messages,
    build_verify_job_status_messages,
    verify_job_status,
    shutdown_mcp_tools,
    human_in_the_loop,
    fetch_jobright_jobs,
    prepare_job_application_queue,
    dispatch_next_job,
)
from langgraph.graph.message import MessagesState
from typing import Literal
from prompts import load_prompt
from utilities import route_application_access, route_user_request
from utilities.routing import (
    conditional_edge_submit_job_application,
    route_after_dispatch,
    route_after_job_status_update,
    route_after_validation,
    route_after_verification,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
import aiosqlite
from typing import Annotated
from operator import add
from langgraph.types import Command


class MyState(MessagesState):
    collect_user_request_messages: Annotated[list[dict], add] = []
    submit_job_application_messages: Annotated[list[dict], add] = []
    validate_job_listing_messages: Annotated[list[dict], add] = []
    resolve_application_url_messages: Annotated[list[dict], add] = []
    authenticate_with_job_site_messages: Annotated[list[dict], add] = []
    verify_job_status_messages: Annotated[list[dict], add] = []
    user_profile: str = ""
    resume_data: str = ""
    resume_file_path: str = ""
    current_state: str = ""

    #user request phase output
    job_url: str = ""
    user_request_phase: Literal["NEEDS_TOOLS", "CHAT", "FETCH_JOB_DESCRIPTION", "DISPATCH_JOBS", "FETCH_JOBRIGHT_JOBS", "END"] = "CHAT"
    num_jobs_to_process: int = 0
    job_query_filter: JobQueryFilter = None
    application_queue: list[dict] = list()
    current_job: dict = None
    job_details: dict = None
    has_next_job: bool = False

    # Fetch JobRight Jobs
    jobright_jobs: list[dict] = list()
    jobright_job_count: int = 5
    
    excel_file_path: str = ""
    is_valid: bool = False
    requires_login: bool = False
    actual_job_url: str = ""
    is_logged_in: bool = False
    logged_in_url: str = ""
    is_duplicate: bool = False
    application_status: str = ""
    final_status_message: str = ""
    requires_human: bool = False
    application_messages: list[dict] = []
    tool_call_origin: str = ""

    # Human Review fields
    human_intervention_reason: str = ""
    human_intervention_prompt: str = ""
    human_intervention_origin: str = ""

    # Application completed fields
    validation_phase : Literal["NEEDS_TOOLS", "ELIGIBLE", "NOT_ELIGIBLE", "END"] = "ELIGIBLE"
    verification_phase : Literal["NEEDS_TOOLS", "APPLIED", "NOT_APPLIED", "END"] = "NOT_APPLIED"
    is_applied: bool = False
    reason_for_not_applying: str = ""
    url_applied_on: str = ""

graph = StateGraph(MyState)


graph.add_node(initialize_user_data)
graph.add_node(collect_user_request)
graph.add_node(fetch_jobright_jobs)
graph.add_node(extract_job_details)
graph.add_node(save_job_to_workbook)
graph.add_node(validate_job_listing)
graph.add_node(resolve_application_url)
graph.add_node(authenticate_with_job_site)
graph.add_node(submit_job_application)
graph.add_node(update_job_status)
graph.add_node(verify_job_status)
graph.add_node("tool_node", tool_node)
graph.add_node(intermediate_node_for_submit_job_application)
graph.add_node(user_request_llm_node)
graph.add_node(build_application_url_messages)
graph.add_node(build_validation_messages)
graph.add_node(build_authenticate_with_job_site_messages)
graph.add_node(build_verify_job_status_messages)
graph.add_node(human_in_the_loop)
graph.add_node(prepare_job_application_queue)
graph.add_node(dispatch_next_job)

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
graph.add_edge("prepare_job_application_queue", "dispatch_next_job")
graph.add_conditional_edges(
    "dispatch_next_job",
    route_after_dispatch,
    {
        "build_validation_messages": "build_validation_messages",
        "collect_user_request": "collect_user_request",
    },
)
graph.add_edge("fetch_jobright_jobs", "collect_user_request")
graph.add_edge("build_validation_messages", "validate_job_listing")
graph.add_edge("extract_job_details", "save_job_to_workbook")
graph.add_edge("save_job_to_workbook", "build_validation_messages")
graph.add_edge("build_application_url_messages", "resolve_application_url")
graph.add_conditional_edges(
    "update_job_status",
    route_after_job_status_update,
    {
        "dispatch_next_job": "dispatch_next_job",
        "collect_user_request": "collect_user_request",
    },
)

graph.add_conditional_edges(
    "validate_job_listing",
    route_after_validation,
    {
        "tool_node": "tool_node",
        "build_application_url_messages": "build_application_url_messages",
        "update_job_status": "update_job_status",
    },
)

graph.add_conditional_edges(
    "resolve_application_url",
    lambda state: state["next_step"],
    {
        "tool_node": "tool_node",
        "intermediate_node_for_submit_job_application": "intermediate_node_for_submit_job_application",
    },
)

graph.add_conditional_edges(
    "authenticate_with_job_site",
    lambda state: state["next_step"],
    {
        "tool_node": "tool_node",
        "submit_job_application": "submit_job_application",
        "update_job_status": "update_job_status",
    },
)

graph.add_conditional_edges(
    "submit_job_application",
    conditional_edge_submit_job_application,
    {
        "tool_node": "tool_node",
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
        "validate_job_listing": "validate_job_listing",
        "resolve_application_url": "resolve_application_url",
        "authenticate_with_job_site": "authenticate_with_job_site",
        "submit_job_application": "submit_job_application",
        "verify_job_status": "verify_job_status",
    },
)

graph.add_conditional_edges(
    "intermediate_node_for_submit_job_application",
    route_application_access,
    {
        "authenticate_with_job_site": "build_authenticate_with_job_site_messages",
        "submit_job_application": "submit_job_application",
    },
)

graph.add_edge("build_authenticate_with_job_site_messages", "authenticate_with_job_site")
graph.add_conditional_edges("human_in_the_loop", lambda state : state["human_intervention_origin"], {
    "submit_job_application": "submit_job_application",
    "resolve_application_url": "resolve_application_url",
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
    checkpointer = AsyncSqliteSaver(conn)
    workflow = graph.compile(checkpointer=checkpointer)


    messages = [
        {"role": "system", "content": load_prompt("chat_with_user/system.txt")},
    ]

    config = {"configurable": {"thread_id": "115"}}

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
