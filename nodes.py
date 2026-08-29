
from mcp_connection import mcp_manager
from langchain_core import prompt_values
from langchain_openrouter import ChatOpenRouter
from tavily import TavilyClient
import logging
import json
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from models import (
    ApplicationAccessStatus,
    AuthenticationResult,
    JobListingDetails,
    JobValidationResult,
    UserRequestRoutingDecision,
    ApplicationResult
)
from prompts import load_prompt
from utilities import (
    await_with_progress,
    get_jobs,
    log_node_stage,
    log_tool_calls,
    log_tool_result,
    update_job_status as update_job_status_tool,
)
from workbook.job_identity import canonical_job_id
from workbook.jobs_workbook import upsert_job_row
from jobs.fetcher import APPLICATION_METRIC_COLUMNS, JOB_COLUMNS
from playwright.async_api import async_playwright
import pandas as pd
from mcp_connection.mcp_manager import MCPManager



model = ChatOpenRouter(
    model="deepseek/deepseek-v4-flash",
    api_key = os.getenv("OPENROUTER_API_KEY")
)

tools_by_name = {
    "get_jobs": get_jobs
}


logger = logging.getLogger(__name__)


def initialize_user_data(state):
    """Load user profile, resume, and file paths so the graph is self-contained.

    This node runs first, making the graph work both from main() and
    LangGraph Studio without requiring external input.
    """
    node_name = "initialize_user_data"
    log_node_stage(node_name, "Started — loading user data")

    project_root = Path(__file__).resolve().parent

    # Load user profile
    with (project_root / "user/user_profile.json").open(encoding="utf-8") as f:
        user_profile = json.load(f)

    # Load resume text
    resume_data = (project_root / "user/resume.txt").read_text(encoding="utf-8")

    # Resolve resume PDF path
    resume_file_path = project_root / "user/resume.pdf"
    if not resume_file_path.is_file():
        raise FileNotFoundError(f"Resume file not found: {resume_file_path}")

    # Excel workbook path
    excel_file_path = str(project_root / "data/jobs.xlsx")

    log_node_stage(node_name, "Completed — user data loaded")

    return {
        "user_profile": user_profile,
        "resume_data": resume_data,
        "resume_file_path": str(resume_file_path),
        "excel_file_path": excel_file_path,
    }


async def collect_user_request(state):
    # Logging
    node_name = "collect_user_request"
    log_node_stage(node_name, "Started — waiting for user input")

    # Defining model
    model_with_tools = model.bind_tools(tools = [get_jobs])
    structured_model = model.with_structured_output(UserRequestRoutingDecision)

    # Getting the arguments
    user_profile, resume, messages = state["user_profile"], state["resume_data"], state["messages"]

    user_input = input("You : > ")

    messages.append({"role": "user", "content": user_input})

    while True:
        response = await await_with_progress(
            node_name,
            "Agent is thinking",
            model_with_tools.ainvoke(messages),
        )
        messages.append(response)

        if response.tool_calls:
            log_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await await_with_progress(
                    node_name,
                    f"Running tool: {tool_name}",
                    tools_by_name[tool_name].ainvoke(tool_args),
                    show_progress=False,
                )
                log_tool_result(node_name, tool_name, tool_response)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            log_node_stage(node_name, "Agent response complete — producing routing decision")
            structured_response = await await_with_progress(
                node_name,
                "Agent is choosing the next step",
                structured_model.ainvoke(messages),
            )
            print(structured_response.message)
            logger.debug("Next action: %s", structured_response.next_action)

            messages.append(structured_response.message)
            break

    return {"messages": messages,
            "next_action": structured_response.next_action,
            "job_url": structured_response.custom_job_url,
            "num_jobs_to_process": structured_response.num_jobs_to_process,
            "excel_jobs_list": structured_response.excel_jobs_list
            }





async def extract_job_details(state):
    # Logging
    node_name = "extract_job_details"
    log_node_stage(node_name, "Started — loading the job page")

    # Defining model
    structured_job_model = model.with_structured_output(JobListingDetails)

    # Getting the arguments
    job_url = state['job_url']

    logger.debug("Job URL: %s", job_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page()

        await page.goto(
            job_url,
            wait_until="domcontentloaded",
            timeout=30_000
        )

        await page.wait_for_timeout(2000)

        page_text = await page.locator("body").inner_text()


        messages = [
            {"role": "system", "content": load_prompt("fetch_job_description/system.txt")},
            {"role": "user", "content": load_prompt(
                "fetch_job_description/user.txt",
                job_url=job_url,
                page_text=page_text,
            )}
        ]

        job_details = await await_with_progress(
            node_name,
            "Agent is extracting job details",
            structured_job_model.ainvoke(messages),
        )

        log_node_stage(node_name, "Completed — job details extracted")
        logger.debug("Extracted job details: %s", job_details)
        await browser.close()

    return {
        "job_details": job_details
    }


def save_job_to_workbook(state):
    # Logging
    node_name = "save_job_to_workbook"
    log_node_stage(node_name, "Started — preparing the spreadsheet update")

    # Getting the arguments
    job_details = state["job_details"]
    excel_file_path = state["excel_file_path"]
    job_dict = job_details.model_dump() if hasattr(job_details, "model_dump") else dict(job_details)
    job_dict["link"] = job_dict.get("link") or state.get("job_url", "")
    job_dict["applyUrl"] = job_dict.get("applyUrl") or state.get("actual_job_url", "")

    row = {column: job_dict.get(column, "") or "" for column in JOB_COLUMNS}
    row["id"] = canonical_job_id(job_dict)
    if not row["id"]:
        raise ValueError("Cannot save a user-supplied job without a stable job URL or ID.")
    row["application_status"] = row["application_status"] or "Not Applied"
    row["fetched_at"] = row["fetched_at"] or pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    for column in APPLICATION_METRIC_COLUMNS:
        row[column] = ""

    application_fields = {
        "application_status", "status", "reason", *APPLICATION_METRIC_COLUMNS,
    }
    action, _ = upsert_job_row(
        Path(excel_file_path), row, preserve_existing=application_fields,
    )
    log_node_stage(node_name, f"Completed — job {action} in the spreadsheet")

    return {}



async def validate_job_listing(state):
    # Logging
    node_name = "validate_job_listing"
    log_node_stage(node_name, "Started — connecting to browser tools")

    # Getting the browser tools
    mcp_manager = MCPManager(["chrome"])
    tools = await mcp_manager.start()

    # Defining model with tools
    structured_model = model.with_structured_output(JobValidationResult)
    model_with_tools = model.bind_tools(tools = tools)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    job_url = state['job_url']
    logger.debug("Job URL: %s", job_url)

    messages = [
        {'role': 'system', 'content': load_prompt("job_verifier/system.txt")},
        {'role':'user', 'content': load_prompt("job_verifier/user.txt", job_url=job_url)}
    ]

    while True:

        response = await await_with_progress(
            node_name,
            "Agent is inspecting the job listing",
            model_with_tools.ainvoke(messages),
        )
        messages.append(response)
        if response.tool_calls:
            log_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await await_with_progress(
                    node_name,
                    f"Running tool: {tool_name}",
                    tools_by_name[tool_name].ainvoke(tool_args),
                    show_progress=False,
                )
                log_tool_result(node_name, tool_name, tool_response)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            log_node_stage(node_name, "Completed — evaluating job eligibility")
            structured_response = await await_with_progress(
                node_name,
                "Agent is evaluating job eligibility",
                structured_model.ainvoke(messages),
            )
            logger.debug("Final response: %s", structured_response)
            break

    await mcp_manager.stop()
    return {
        "is_valid": structured_response.is_valid
    }


async def resolve_application_url(state):
    # Logging
    node_name = "resolve_application_url"
    log_node_stage(node_name, "Started — locating the application page")

    # Getting the browser tools
    mcp_manager = MCPManager(["chrome"])
    tools = await mcp_manager.start()
    logger.debug("Browser tools available: %s", ', '.join(tool.name for tool in tools))

    # Defining model with tools
    model_with_tools = model.bind_tools(tools = tools)
    structured_model = model.with_structured_output(ApplicationAccessStatus)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    job_url = state['job_url']
    logger.debug("Job URL: %s", job_url)

    messages = [
        {'role': 'system', 'content': load_prompt("navigate_to_application/system.txt")},
        {'role':'user', 'content': load_prompt("navigate_to_application/user.txt", job_url=job_url)}
    ]

    while True:

        response = await await_with_progress(
            node_name,
            "Agent is finding the application page",
            model_with_tools.ainvoke(messages),
        )
        messages.append(response)
        if response.tool_calls:
            log_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await await_with_progress(
                    node_name,
                    f"Running tool: {tool_name}",
                    tools_by_name[tool_name].ainvoke(tool_args),
                    show_progress=False,
                )
                log_tool_result(node_name, tool_name, tool_response)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            log_node_stage(node_name, "Completed — checking login requirement")
            structured_response = await await_with_progress(
                node_name,
                "Agent is checking whether sign-in is required",
                structured_model.ainvoke(messages),
            )
            logger.debug("Final response: %s", structured_response)
            break

    await mcp_manager.stop()
    return {
        "requires_login": structured_response.requires_login,
        "actual_job_url": structured_response.actual_job_url,
    }




async def authenticate_with_job_site(state):
    # Logging
    node_name = "authenticate_with_job_site"
    log_node_stage(node_name, "Started — signing in to the application site")

    job_url = state['actual_job_url']

    mcp_manager = MCPManager(["chrome", "gmail"])
    tools = await mcp_manager.start()
    logger.debug("Browser/email tools available: %s", ', '.join(tool.name for tool in tools))

    # Defining model with tools
    structured_login_output = model.with_structured_output(AuthenticationResult)
    model_with_tools = model.bind_tools(tools = tools)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    messages = [
        {'role': 'system', 'content': load_prompt("perform_login/system.txt")},
        {'role':'user', 'content': load_prompt("perform_login/user.txt", job_url=job_url)}
    ]

    while True:

        response = await await_with_progress(
            node_name,
            "Agent is handling sign-in",
            model_with_tools.ainvoke(messages),
        )
        messages.append(response)
        if response.tool_calls:
            log_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await await_with_progress(
                    node_name,
                    f"Running tool: {tool_name}",
                    tools_by_name[tool_name].ainvoke(tool_args),
                    show_progress=False,
                )
                log_tool_result(node_name, tool_name, tool_response)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            log_node_stage(node_name, "Completed — recording login result")
            structured_response = await await_with_progress(
                node_name,
                "Agent is recording the sign-in result",
                structured_login_output.ainvoke(messages),
            )
            logger.debug("Final response: %s", structured_response)
            break

    await mcp_manager.stop()



async def submit_job_application(state):
    node_name = "submit_job_application"
    log_node_stage(node_name, "Started — completing the job application")
    job_url = state["actual_job_url"]

    mcp_manager = MCPManager(["chrome"])
    tools = await mcp_manager.start()
    logger.debug("Browser tools available: %s", ', '.join(tool.name for tool in tools))
    model_with_tools = model.bind_tools(tools = tools)
    structured_model = model.with_structured_output(ApplicationResult)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    messages = [
        {'role': 'system', 'content': load_prompt("apply_for_job/system.txt")},
        {'role':'user', 'content': load_prompt(
            "apply_for_job/user.txt",
            job_url=job_url,
            user_profile=state['user_profile'],
            resume_data=state['resume_data'],
            resume_file_path=state['resume_file_path'],
        )}
    ]

    while True:

        response = await await_with_progress(
            node_name,
            "Agent is thinking",
            model_with_tools.ainvoke(messages),
        )
        messages.append(response)
        if response.tool_calls:
            log_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await await_with_progress(
                    node_name,
                    f"Running tool: {tool_name}",
                    tools_by_name[tool_name].ainvoke(tool_args),
                    show_progress=False,
                )
                log_tool_result(node_name, tool_name, tool_response)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            log_node_stage(node_name, "Completed — recording application status")
            structured_response = await await_with_progress(
                node_name,
                "Agent is recording the application status",
                structured_model.ainvoke(messages),
            )
            logger.debug("Final response: %s", structured_response)
            break

    await mcp_manager.stop()
    return {
        "is_applied": structured_response.is_applied,
        "url_applied_on": structured_response.url_applied_on,
        "final_status_message": structured_response.final_status_message,
        "is_duplicate": structured_response.is_duplicate,
    }




async def verify_job_success(state):
    node_name = "verify_job_success"
    log_node_stage(node_name, "Started — verifying job application success")

    job_url = state['url_applied_on']

    mcp_manager = MCPManager(["chrome"])
    tools = await mcp_manager.start()
    logger.debug("Browser tools available: %s", ', '.join(tool.name for tool in tools))

    # Defining model with tools
    model_with_tools = model.bind_tools(tools = tools)

    global tools_by_name

    for tool in tools:
        tools_by_name[tool.name] = tool

    messages = [
        {'role': 'system', 'content': load_prompt("verify_application/system.txt")},
        {'role':'user', 'content': load_prompt(
            "verify_application/user.txt",
            application_url=state["url_applied_on"],
            is_applied=state["is_applied"],
        )}
    ]

    while True:

        response = await await_with_progress(
            node_name,
            "Agent is verifying the application success",
            model_with_tools.ainvoke(messages),
        )
        messages.append(response)
        if response.tool_calls:
            log_tool_calls(node_name, response.tool_calls)
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                tool_response = await await_with_progress(
                    node_name,
                    f"Running tool: {tool_name}",
                    tools_by_name[tool_name].ainvoke(tool_args),
                    show_progress=False,
                )
                log_tool_result(node_name, tool_name, tool_response)

                messages.append({"role" : "tool", "content" : str(tool_response), "tool_call_id" : tool_call['id']})
        else:
            log_node_stage(node_name, "Completed — returning verification result")
            break

    await mcp_manager.stop()
    return {
        "application_status": response.application_status,
    }


async def update_job_status(state):
    """Persist the result of an application by directly invoking the status tool."""
    node_name = "update_job_status"
    log_node_stage(node_name, "Started — updating the job status")

    job_details = state.get("job_details", {})
    if hasattr(job_details, "model_dump"):
        job_details = job_details.model_dump()

    job_record = {
        **job_details,
        "link": job_details.get("link") or state.get("job_url", ""),
        "applyUrl": job_details.get("applyUrl") or state.get("actual_job_url", ""),
    }
    job_id = canonical_job_id(job_record)
    if not job_id:
        raise ValueError("Cannot update job status because the job has no stable ID.")

    if state.get("is_applied") or state.get("is_duplicate"):
        application_status = "Applied"
    else:
        application_status = "Not Applied"

    final_status_message = state.get("final_status_message", "")
    tool_args = {
        "job_id": job_id,
        "application_status": application_status,
        "status": final_status_message,
        "reason": final_status_message if application_status != "Applied" else "",
    }
    log_tool_calls(node_name, [{"name": update_job_status_tool.name, "args": tool_args}])
    tool_result = await await_with_progress(
        node_name,
        "Saving application status",
        update_job_status_tool.ainvoke(tool_args),
        show_progress=False,
    )
    log_tool_result(node_name, update_job_status_tool.name, tool_result)

    if "error" in tool_result:
        raise RuntimeError(tool_result["error"])

    log_node_stage(node_name, "Completed — job status updated")
    return {"application_status": application_status}
