from langgraph.graph import StateGraph, START, END
from nodes import chat_with_user, fetch_job_description, insert_into_excel_file, job_verifier_node, navigate_to_application, perform_login, apply_for_job
from langgraph.graph.message import MessagesState
from typing import Literal
from conditions import condition, login_condition
from prompts import load_prompt
import json

class MyState(MessagesState):
    user_profile: str
    resume_data: str
    resume_file_path: str
    next_action: Literal["CHAT", "USE_CUSTOM_JOB_URL", "USE_EXCEL_JOBS"]
    job_url: str
    num_jobs_to_process: int
    excel_jobs_list: list[dict]
    job_details: dict
    excel_file_path : str
    is_valid: bool
    requires_login: bool
    actual_job_url: str
    is_logged_in: bool
    logged_in_url: str


graph = StateGraph(MyState)


graph.add_node(chat_with_user)
graph.add_node(fetch_job_description)
graph.add_node(insert_into_excel_file)
graph.add_node(job_verifier_node)
graph.add_node(navigate_to_application)
graph.add_node(perform_login)
graph.add_node(apply_for_job)

graph.add_edge(START, "chat_with_user")
graph.add_conditional_edges(
    "chat_with_user",
    condition,
    {"chat_with_user": "chat_with_user", "fetch_job_description": "fetch_job_description", "END": END}
)
graph.add_edge("fetch_job_description", "insert_into_excel_file")
graph.add_edge("insert_into_excel_file", "job_verifier_node")
graph.add_edge("job_verifier_node", "navigate_to_application")
graph.add_conditional_edges(
    "navigate_to_application",
    login_condition,
    {"perform_login": "perform_login", "apply_for_job": "apply_for_job"}
)



app = graph.compile()

async def main():

    messages = [
            {"role": "system", "content": load_prompt("chat_with_user/system.txt")},
        ]

    user_profile = json.load(open('user/user_profile.json', 'r'))

    resume_data = open('user/resume.txt', 'r').read()

    resume_file_path = 'data/resume.pdf'



    await app.ainvoke({
        "user_profile" : user_profile,
        "resume_data" : resume_data,
        "resume_file_path" : resume_file_path,
        "messages": messages,
        "next_action": "CHAT",
        "job_url": "",
        "num_jobs_to_process": 0,
        "excel_jobs_list": [],
        "excel_file_path": 'data/jobs.xlsx',
        "job_details": {}
    })

import asyncio
if __name__ == "__main__":
    asyncio.run(main())
