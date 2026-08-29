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
)
from langgraph.graph.message import MessagesState
from typing import Literal
from prompts import load_prompt
from utilities import route_application_access, route_user_request

class MyState(MessagesState):
    user_profile: str = ""
    resume_data: str = ""
    resume_file_path: str = ""
    next_action: Literal["CHAT", "USE_CUSTOM_JOB_URL", "USE_EXCEL_JOBS"] = "CHAT"
    job_url: str = ""
    job_id: str = ""
    num_jobs_to_process: int = 0
    excel_jobs_list: list[dict] = []
    job_details: dict = {}
    excel_file_path: str = ""
    is_valid: bool = False
    requires_login: bool = False
    actual_job_url: str = ""
    is_logged_in: bool = False
    logged_in_url: str = ""
    is_applied: bool = False
    final_status_message: str = ""
    application_status: str = ""


graph = StateGraph(MyState)


graph.add_node(initialize_user_data)
graph.add_node(collect_user_request)
graph.add_node(extract_job_details)
graph.add_node(save_job_to_workbook)
graph.add_node(validate_job_listing)
graph.add_node(resolve_application_url)
graph.add_node(authenticate_with_job_site)
graph.add_node(submit_job_application)
graph.add_node(update_job_status)

graph.add_edge(START, "initialize_user_data")
graph.add_edge("initialize_user_data", "collect_user_request")
graph.add_conditional_edges(
    "collect_user_request",
    route_user_request,
    {
        "collect_user_request": "collect_user_request",
        "extract_job_details": "extract_job_details",
        "END": END,
    },
)
graph.add_edge("authenticate_with_job_site", "submit_job_application")
graph.add_edge("submit_job_application", "update_job_status")
graph.add_edge("update_job_status", END)
graph.add_edge("extract_job_details", "save_job_to_workbook")
graph.add_edge("save_job_to_workbook", "validate_job_listing")
graph.add_edge("validate_job_listing", "resolve_application_url")
graph.add_conditional_edges(
    "resolve_application_url",
    route_application_access,
    {
        "authenticate_with_job_site": "authenticate_with_job_site",
        "submit_job_application": "submit_job_application",
    },
)



app = graph.compile()

async def main():
    """Run the graph from the command line.

    User data is loaded automatically by the initialize_user_data node,
    so we only need to provide the system prompt message.
    """
    messages = [
        {"role": "system", "content": load_prompt("chat_with_user/system.txt")},
    ]

    await app.ainvoke({"messages": messages})


import asyncio
if __name__ == "__main__":
    asyncio.run(main())
