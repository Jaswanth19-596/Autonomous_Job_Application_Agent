

from collections import deque
import asyncio
from langgraph.types import interrupt
from mcp_connection import mcp_manager
from langchain_openrouter import ChatOpenRouter
import logging
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
load_dotenv()
from models import (
    ApplicationAccessStatus,
    AuthenticationResult,
    JobListingDetails,
    JobValidationResult,
    UserRequestRoutingDecision,
    ApplicationResult,
    ApplicationVerificationResult,
    JobrightJob,
)
from prompts import load_prompt
from utilities import (
    await_with_progress,
    get_jobs,
    llm_logger_handler,
    log_node_stage,
    log_tool_calls,
    log_tool_result,
    log_workflow_event,
    trace_node,
    update_job_status as update_job_status_tool,
)
from workbook.job_identity import canonical_job_id
from workbook.jobs_workbook import upsert_job_row, upsert_job_rows
from jobs.fetcher import APPLICATION_METRIC_COLUMNS, JOB_COLUMNS
from jobs.jobright import (
    JOBRIGHT_RECOMMENDED_URL,
    format_recent_workbook_jobs_for_prompt,
    recent_workbook_job_summaries,
    workbook_rows_from_jobright_jobs,
)
from playwright.async_api import async_playwright
import pandas as pd
from mcp_connection.mcp_manager import MCPManager
from langgraph.prebuilt import ToolNode
from langchain.messages import ToolMessage
from models.schemas import JobQueryFilter

# OpenRouter provider slugs are verified against the model's endpoint list. Keep
# this list in sync with the output-token price ceiling below.
MAX_OUTPUT_PRICE_PER_MILLION_TOKENS = 0.28
WANTED_OPENROUTER_PROVIDERS = [
    "open-inference",  # $0.16/M output
    "sail-research",  # $0.18/M output
    "akashml",  # $0.18/M output
    "relace",  # $0.18/M output
    "deepinfra",  # $0.18/M output
    "ambient",  # $0.18/M output
    "makora",  # $0.195/M output
    "wafer",  # $0.25/M output
    "digitalocean",  # $0.252/M output
    "baseten",  # $0.26/M output
    "morph",  # $0.278/M output
    "inceptron",  # $0.28/M output
    "coreweave",  # $0.28/M output
    "baidu",  # $0.28/M output
    "parasail",  # $0.28/M output
    "together",  # $0.28/M output
]
UNWANTED_OPENROUTER_PROVIDERS = [
    "venice",
    "mancer",
    "streamlake",
    "fireworks",
    "reka",
    "siliconflow",
    "gmicloud",
    "alibaba",
    "nextbit",
    "novita",
    "atlas-cloud",
    "phala",
    "cloudflare",
    "deepseek",
]

model = ChatOpenRouter(
    model="deepseek/deepseek-v4-flash-0731",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    openrouter_provider={
        "only": WANTED_OPENROUTER_PROVIDERS,
        "ignore": UNWANTED_OPENROUTER_PROVIDERS,
    },
)


mcp_manager = MCPManager()


async def get_custom_model_with_tools(tool_list):
    """Load requested MCP tools before binding them to the model."""
    mcp_servers = [tool_name for tool_name in tool_list if tool_name in {"chrome", "gmail"}]
    mcp_tools = await mcp_manager.start(mcp_servers) if mcp_servers else []

    for tool in mcp_tools:
        tools_by_name[tool.name] = tool

    tools = [*mcp_tools]
    if "custom" in tool_list:
        tools.append(get_jobs)
    return model.bind_tools(tools=tools)

def get_custom_structured_model(model_type):
    if model_type == "application_model":
        return model.with_structured_output(ApplicationResult)
    elif model_type == "user_request_model":
        return model.with_structured_output(UserRequestRoutingDecision)
    elif model_type == "job_listing_model":
        return model.with_structured_output(JobListingDetails)
    elif model_type == "job_validation_model":
        return model.with_structured_output(JobValidationResult)
    elif model_type == "authentication_model":
        return model.with_structured_output(AuthenticationResult)
    elif model_type == "application_access_model":
        return model.with_structured_output(ApplicationAccessStatus)
    elif model_type == "application_verification_model":
        return model.with_structured_output(ApplicationVerificationResult)
    elif model_type == "jobright_job_model":
        return model.with_structured_output(JobrightJob)
    else:
        return None
    




# application_model = model.with_structured_output(ApplicationResult)

tools_by_name = {
    "get_jobs": get_jobs,
}



logger = logging.getLogger(__name__)


async def shutdown_mcp_tools():
    """Stop the manager-owned persistent MCP sessions after a workflow run."""
    await mcp_manager.stop()


def is_http_job_url(value: str) -> bool:
    """Return whether a routing decision contains a usable job-posting URL."""
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@trace_node
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

    # System prompt for the chat node
    system_message = {"role": "system", "content": load_prompt("chat_with_user/system.txt")}

    log_node_stage(node_name, "Completed — user data loaded")

    return {
        "user_profile": user_profile,
        "resume_data": resume_data,
        "resume_file_path": str(resume_file_path),
        "excel_file_path": excel_file_path,
        "messages": [system_message],
        "submit_job_application_messages": [
            {'role': 'system', 'content': load_prompt("apply_for_job/system.txt")},
            {'role':'user', 'content': load_prompt(
                "apply_for_job/user.txt",
                user_profile=json.dumps(user_profile),
                resume_data=resume_data,
                resume_file_path=str(resume_file_path),
            )},
        ]
    }



@trace_node
async def tool_node(state):
    origin_node = state.get("tool_call_origin", "unknown_node")
    node_name = f"{origin_node} → tool_node"
    log_node_stage(node_name, "Started — executing tools")

    message_keys = {
        "submit_job_application": "submit_job_application_messages",
        "validate_job_listing": "validate_job_listing_messages",
        "resolve_application_url": "resolve_application_url_messages",
        "authenticate_with_job_site": "authenticate_with_job_site_messages",
        "verify_job_status": "verify_job_status_messages",
    }
    message_key = message_keys.get(state["tool_call_origin"], "messages")
    messages = state[message_key]

    last_message = messages[-1]

    tool_calls = last_message.tool_calls
    log_tool_calls(origin_node, tool_calls)

    tool_messages = []
    for tool_call in tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_call_id = tool_call["id"]
        tool = tools_by_name.get(tool_name)
        if tool is None:
            error = f"Unknown tool requested: {tool_name}"
            logger.warning(error)
            tool_messages.append(
                ToolMessage(content=error, tool_call_id=tool_call_id, name=tool_name)
            )
            continue
        try:
            tool_result = await await_with_progress(
                node_name,
                f"Running tool: {tool_name}",
                tool.ainvoke(tool_args),
                show_progress=False,
            )
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            tool_result = {"error": f"{type(exc).__name__}: {exc}"}
        log_tool_result(node_name, tool_name, tool_result)
        tool_messages.append(
            ToolMessage(
                content=tool_result,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        )
    
    return {
        message_key: tool_messages
    }
    


@trace_node
async def collect_user_request(state):
    # Logging
    node_name = "collect_user_request"
    log_node_stage(node_name, "Started — waiting for user input")

    # Defining model
    # Getting the arguments
    user_profile, resume, messages = state["user_profile"], state["resume_data"], state["messages"]

    user_input = interrupt({
        "prompt": "What's your problem: ",
        }
    )
    user_message = {"role": "user", "content": user_input}

    return {
        "messages": [user_message]
    }



@trace_node
async def user_request_llm_node(state):
    # Logging
    node_name = "user_request_llm_node"
    log_node_stage(node_name, "Started — agent is thinking")

    # Defining model
    model_with_tools = await get_custom_model_with_tools(["custom"])
    structured_model = get_custom_structured_model("user_request_model")

    # Getting the arguments
    messages = state["messages"]

    response = await await_with_progress(
        node_name,
        "Agent is thinking",
        model_with_tools.ainvoke(messages)
    )

    if response.tool_calls:
        log_workflow_event(
            "user request requested tools",
            destination="tool_node",
            tool_count=len(response.tool_calls),
        )
        return {
            "user_request_phase": "NEEDS_TOOLS",
            "tool_call_origin": "user_request_llm_node",
            "messages": [response]
        }

    log_node_stage(node_name, "Agent response complete — producing routing decision")
    structured_messages = [
        {"role": "system", "content": load_prompt("chat_with_user/structured_system.txt")},
        *messages[1:],
        response,
        {
            "role": "user",
            "content": (
                "Based on the conversation above, you MUST return the structured output decision now."
            ),
        },
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is choosing the next step",
        structured_model.ainvoke(structured_messages),
    )

    if structured_response is None:
        logger.warning("Structured model returned None for user request decision; falling back to CHAT")
        structured_response = UserRequestRoutingDecision(next_step="CHAT")

    if response.content:
        print(response.content)

    if structured_response.next_step == "FETCH_JOB_DESCRIPTION":
        job_url = (
            structured_response.fetch_job_description_decision.job_url.strip()
            if structured_response.fetch_job_description_decision
            else ""
        )
        return {
            "messages": [response],
            "user_request_phase": structured_response.next_step,
            "job_url": job_url,
        }

    if structured_response.next_step == "FETCH_JOBRIGHT_JOBS":
        jobright_job_count = (
            structured_response.fetch_jobright_jobs_decision.job_count
            if structured_response.fetch_jobright_jobs_decision
            else 5
        )
        return {
            "messages": [response],
            "user_request_phase": structured_response.next_step,
            "jobright_job_count": jobright_job_count,
        }

    if structured_response.next_step == "DISPATCH_JOBS":
        job_query_filter = (
            structured_response.dispatch_jobs_decision.job_query_filter
            if structured_response.dispatch_jobs_decision and structured_response.dispatch_jobs_decision.job_query_filter
            else JobQueryFilter(query="", n=1)
        )
        return {
            "messages": [response],
            "user_request_phase": structured_response.next_step,
            "job_query_filter": job_query_filter,
        }

    if structured_response.next_step == "CHAT":
        return {
            "messages": [response],
            "user_request_phase": structured_response.next_step,
        }

    if structured_response.next_step == "END":
        return {
            "messages": [response],
            "user_request_phase": structured_response.next_step,
        }

    logger.debug("Next action: %s", structured_response.next_step)




@trace_node
async def prepare_job_application_queue(state):

    job_query_filter = state["job_query_filter"]
    
    tool_args = job_query_filter.model_dump()
    tool_args["filters"] = ["Not Applied"]
    log_workflow_event("loading application queue", **tool_args)

    result = await asyncio.to_thread(get_jobs.invoke, tool_args)

    if "error" in result:
        logger.error(f"Error fetching jobs: {result['error']}")
        log_workflow_event("application queue unavailable", error=result["error"])
        return {
            "application_queue": [],
            "num_jobs_to_process": 0
        }

    else:
        log_workflow_event(
            "application queue ready",
            matched_jobs=result.get("total_matching", len(result["jobs"])),
            queued_jobs=len(result["jobs"]),
        )
        return {
            "application_queue": result["jobs"],
            "num_jobs_to_process": len(result["jobs"])
        }


@trace_node
async def dispatch_next_job(state):
    application_queue = state.get("application_queue", [])
    if not application_queue:
        log_workflow_event("application queue exhausted", destination="collect_user_request")
        return {
            "current_job": None,
            "has_next_job": False,
            "num_jobs_to_process": 0,
        }

    current_job = application_queue[0]
    remaining_application_queue = application_queue[1:]
    job_url = current_job.get("applyUrl") or current_job.get("link", "")
    log_workflow_event(
        "selected queued job for validation",
        job_id=current_job.get("id", ""),
        title=current_job.get("title", ""),
        company=current_job.get("companyName", ""),
        remaining_jobs=len(remaining_application_queue),
    )

    return {
        "current_job": current_job,
        "job_details": current_job,
        "job_url": job_url,
        "application_queue": remaining_application_queue,
        "num_jobs_to_process": len(remaining_application_queue),
        "has_next_job": True,
        "is_valid": False,
        "is_applied": False,
        "is_duplicate": False,
        "requires_login": False,
        "requires_human": False,
        "application_status": "",
        "final_status_message": "",
        "reason_for_not_applying": "",
        "url_applied_on": "",
    }


async def _run_browser_agent(node_name: str, messages: list, max_steps: int = 100) -> list:
    """Let the model complete a bounded browser task through Chrome MCP tools."""
    model_with_tools = await get_custom_model_with_tools(["chrome"])
    for step in range(max_steps):
        step_num = step + 1
        log_node_stage(
            node_name,
            f"Step {step_num}/{max_steps} — awaiting LLM decision ({len(messages)} message(s) in context)",
        )
        response = await await_with_progress(
            node_name,
            f"Browser agent step {step_num}/{max_steps} — model thinking",
            model_with_tools.ainvoke(messages),
        )
        messages.append(response)
        if not response.tool_calls:
            log_node_stage(node_name, f"Step {step_num}/{max_steps} — finished (model returned no tool calls)")
            return messages

        log_node_stage(node_name, f"Step {step_num}/{max_steps} — received {len(response.tool_calls)} tool call(s)")
        log_tool_calls(node_name, response.tool_calls)
        for tool_idx, tool_call in enumerate(response.tool_calls, start=1):
            tool_name = tool_call["name"]
            tool = tools_by_name.get(tool_name)
            if tool is None:
                raise RuntimeError(f"The browser tool '{tool_name}' is unavailable.")
            log_node_stage(
                node_name,
                f"Step {step_num}/{max_steps} [Tool {tool_idx}/{len(response.tool_calls)}] — calling '{tool_name}'",
            )
            tool_result = await await_with_progress(
                node_name,
                f"Running browser tool: {tool_name}",
                tool.ainvoke(tool_call["args"]),
                show_progress=False,
            )
            log_tool_result(node_name, tool_name, tool_result)
            messages.append(
                ToolMessage(
                    content=tool_result,
                    tool_call_id=tool_call["id"],
                    name=tool_name,
                )
            )

    raise RuntimeError(f"The browser agent did not finish the Jobright collection within {max_steps} steps.")


async def collect_jobright_job(
    job_number: int,
    job_count: int,
    excluded_jobs: list[dict[str, str]],
) -> JobrightJob:
    """Collect one eligible Jobright card and return its structured details."""
    if not 1 <= job_count <= 5:
        raise ValueError("Jobright job count must be between 1 and 5.")
    if not 1 <= job_number <= job_count:
        raise ValueError("Jobright job number must be within the requested count.")

    excluded_jobs_text = format_recent_workbook_jobs_for_prompt(excluded_jobs)
    node_name = "fetch_jobright_jobs"
    log_node_stage(
        node_name,
        f"Starting browser agent session for Jobright job {job_number}/{job_count}",
    )
    browser_messages = await _run_browser_agent(
        node_name,
        [
            {
                "role": "system",
                "content": load_prompt(
                    "fetch_jobright_jobs/system.txt",
                    job_number=job_number,
                    job_count=job_count,
                    excluded_jobs=excluded_jobs_text,
                ),
            },
            {
                "role": "user",
                "content": load_prompt(
                    "fetch_jobright_jobs/user.txt",
                    jobright_url=JOBRIGHT_RECOMMENDED_URL,
                    job_number=job_number,
                    job_count=job_count,
                    excluded_jobs=excluded_jobs_text,
                ),
            },
        ],
    )
    log_node_stage(
        node_name,
        f"Browser session completed ({len(browser_messages)} messages). Extracting one structured job...",
    )
    structured_model = get_custom_structured_model("jobright_job_model")
    structured_messages = [
        {
            "role": "system",
            "content": load_prompt(
                "fetch_jobright_jobs/structured_system.txt",
                job_number=job_number,
                job_count=job_count,
                excluded_jobs=excluded_jobs_text,
            ),
        },
        *browser_messages[1:],
        {
            "role": "user",
            "content": (
                "Using only the browser evidence above, return one Jobright job "
                "in the required structured format."
            ),
        },
    ]
    result = await await_with_progress(
        node_name,
        f"Agent is structuring Jobright job {job_number}/{job_count}",
        structured_model.ainvoke(structured_messages),
    )
    if result is None:
        raise RuntimeError("The browser agent did not return a Jobright job.")
    log_node_stage(node_name, f"Successfully structured Jobright job {job_number}/{job_count}")
    return result


@trace_node
async def fetch_jobright_jobs(state):
    """Fetch the requested Jobright matches and persist unique workbook rows."""
    node_name = "fetch_jobright_jobs"
    job_count = state.get("jobright_job_count", 5)
    log_node_stage(
        node_name, f"Started — loading {job_count} Jobright recommendation(s)"
    )

    try:
        workbook_path = Path(state["excel_file_path"])
        excluded_jobs = recent_workbook_job_summaries(workbook_path, limit=20)
        application_fields = {
            "application_status", "status", "reason", *APPLICATION_METRIC_COLUMNS,
        }
        jobright_jobs: list[dict[str, str]] = []
        inserted = updated = 0

        for job_number in range(1, job_count + 1):
            job = await collect_jobright_job(
                job_number, job_count, list(excluded_jobs)
            )
            row = workbook_rows_from_jobright_jobs([job], expected_count=1)[0]
            action, _ = upsert_job_row(
                workbook_path,
                row,
                preserve_existing=application_fields,
            )
            if action == "inserted":
                inserted += 1
            else:
                updated += 1
            job_data = job.model_dump()
            jobright_jobs.append(job_data)
            excluded_jobs.append(
                {"companyName": job_data["companyName"], "title": job_data["title"]}
            )
            log_node_stage(
                node_name,
                f"Saved Jobright job {job_number}/{job_count} before collecting the next one",
            )

        message = (
            f"Saved {len(jobright_jobs)} Jobright job(s) individually to the workbook "
            f"({inserted} added, {updated} refreshed)."
        )
    except Exception as exc:
        logger.warning("Could not fetch all Jobright recommendations: %s", exc)
        saved_count = len(jobright_jobs) if "jobright_jobs" in locals() else 0
        message = (
            f"Stopped Jobright collection after saving {saved_count} job(s): {exc}"
        )
        jobright_jobs = jobright_jobs if "jobright_jobs" in locals() else []

    log_node_stage(node_name, f"Completed — {message}")
    print(message)
    return {
        "jobright_jobs": jobright_jobs,
        "messages": [{"role": "assistant", "content": message}],
    }





@trace_node
async def extract_job_details(state):
    # Logging
    node_name = "extract_job_details"
    log_node_stage(node_name, "Started — loading the job page")

    # Defining model
    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("job_listing_model")

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
            structured_model.ainvoke(messages),
        )

        log_node_stage(node_name, "Completed — job details extracted")
        logger.debug("Extracted job details: %s", job_details)
        await browser.close()

    return {
        "job_details": job_details
    }


@trace_node
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
    write_result = upsert_job_rows(
        Path(excel_file_path), [row], preserve_existing=application_fields,
    )
    action = "inserted" if write_result.inserted else "updated"
    log_node_stage(node_name, f"Completed — job {action} in the spreadsheet")

    return {}


@trace_node
def build_validation_messages(state):

    job_url = state.get("job_url")
    log_workflow_event(
        "building validation prompt",
        job_id=(state.get("current_job") or {}).get("id", ""),
        job_host=urlparse(job_url).netloc if job_url else "",
    )

    return {
        "validate_job_listing_messages" : [
            {'role': 'system', 'content': load_prompt("job_verifier/system.txt")},
            {'role':'user', 'content': load_prompt("job_verifier/user.txt", job_url = job_url)}
        ]
    }
        
    
@trace_node
async def validate_job_listing(state):
    # Logging
    node_name = "validate_job_listing"
    log_node_stage(node_name, "Started — connecting to browser tools")


    # Defining model with tools
    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("job_validation_model")

    messages = state["validate_job_listing_messages"]

    response = await await_with_progress(
        node_name,
        "Agent is inspecting the job listing",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        return {
            "validation_phase": "NEEDS_TOOLS",
            "tool_call_origin": "validate_job_listing",
            "validate_job_listing_messages": [response]
        }

    log_node_stage(node_name, "Completed — evaluating job eligibility")
    structured_messages = [
        {"role": "system", "content": load_prompt("job_verifier/structured_system.txt")},
        *messages[1:],
        response,
        {
            "role": "user",
            "content": (
                "Based on the browsing evidence above, you MUST return the structured output evaluation now."
            ),
        },
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is evaluating job eligibility",
        structured_model.ainvoke(structured_messages),
    )
    logger.debug("Final response: %s", structured_response)

    return {
        "validation_phase": "ELIGIBLE" if structured_response.is_valid else "NOT_ELIGIBLE",
        "reason_for_not_applying": structured_response.reason_for_not_applying,
        "final_status_message": structured_response.reason_for_not_applying,
        "is_applied": False,
    }

@trace_node
def build_application_url_messages(state):

    job_url = state.get("job_url")

    return {
        "resolve_application_url_messages" : [
            {'role': 'system', 'content': load_prompt("navigate_to_application/system.txt")},
            {'role':'user', 'content': load_prompt("navigate_to_application/user.txt", job_url = job_url)}
        ]
    }



@trace_node
async def resolve_application_url(state):
    # Logging
    node_name = "resolve_application_url"
    log_node_stage(node_name, "Started — locating the application page")

    # Defining model with tools
    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("application_access_model")

    messages = state["resolve_application_url_messages"]


    response = await await_with_progress(
        node_name,
        "Agent is finding the application page",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        return {
            "next_step": "tool_node",
            "tool_call_origin": "resolve_application_url",
            "resolve_application_url_messages": [response]
        }
        
    log_node_stage(node_name, "Completed — checking login requirement")
    structured_messages = [
        {"role": "system", "content": load_prompt("navigate_to_application/structured_system.txt")},
        *messages[1:],
        response,
        {
            "role": "user",
            "content": (
                "Based on the browsing evidence above, you MUST return the structured output evaluation now."
            ),
        },
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is checking whether sign-in is required",
        structured_model.ainvoke(structured_messages),
    )

    retry = 3
    while structured_response is None and retry > 0:
        print("Re-running structured model for login check")
        structured_response = await await_with_progress(
            node_name,
            "Agent is checking whether sign-in is required",
            structured_model.ainvoke(structured_messages),
        )
        retry -= 1

    if structured_response is None:
        raise ValueError("Model didn't produce the structured output")

    logger.debug("Final response: %s", structured_response)

    return {
        "next_step": "intermediate_node_for_submit_job_application",
        "requires_login": structured_response.requires_login,
        "actual_job_url": structured_response.actual_job_url,
        "resolve_application_url_messages":  [structured_response.message]
    }



@trace_node
def build_authenticate_with_job_site_messages(state):

    job_url = state.get("actual_job_url")

    return {
        "authenticate_with_job_site_messages" : [
            {'role': 'system', 'content': load_prompt("perform_login/system.txt")},
            {'role':'user', 'content': load_prompt("perform_login/user.txt", job_url = job_url)}
        ]
    }


@trace_node
async def authenticate_with_job_site(state):
    # Logging
    node_name = "authenticate_with_job_site"
    log_node_stage(node_name, "Started — signing in to the application site")

    # Defining model with tools
    model_with_tools = await get_custom_model_with_tools(["chrome", "gmail"])
    structured_model = get_custom_structured_model("authentication_model")

    messages = state["authenticate_with_job_site_messages"]

    response = await await_with_progress(
        node_name,
        "Agent is handling sign-in",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        return {
            "next_step": "tool_node",
            "tool_call_origin": "authenticate_with_job_site",
            "authenticate_with_job_site_messages": [response]
        }

    log_node_stage(node_name, "Completed — evaluating authentication")
    structured_messages = [
        {"role": "system", "content": load_prompt("perform_login/structured_system.txt")},
        *messages[1:],
        response,
        {
            "role": "user",
            "content": (
                "Based on the browsing evidence above, you MUST return the structured output evaluation now."
            ),
        },
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is evaluating authentication",
        structured_model.ainvoke(structured_messages),
    )
    logger.debug("Final response: %s", structured_response)

    next_step = "submit_job_application" if structured_response.is_logged_in else "update_job_status"
    return {
        "next_step": next_step,
        "is_logged_in": structured_response.is_logged_in,
        "logged_in_url": structured_response.logged_in_url,
        "final_status_message": (
            "" if structured_response.is_logged_in
            else "Unable to sign in to the application site."
        ),
        "authenticate_with_job_site_messages": [structured_response.message]
    }


@trace_node
async def intermediate_node_for_submit_job_application(state):
    job_url = state["actual_job_url"]
    return {
        'submit_job_application_messages': [
            {
                "role": "user",
                "content": f"The Actual Job URL is : {job_url}\n"
            }
        ]
    }

@trace_node
async def submit_job_application(state):
    node_name = "submit_job_application"
    log_node_stage(node_name, "Started — completing the job application")
    
    messages = state['submit_job_application_messages']

    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_application_model = get_custom_structured_model("application_model")

    response = await await_with_progress(
        node_name,
        "Agent is thinking",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        return {
            "next_step": "tool_node",
            "tool_call_origin": "submit_job_application",
            "submit_job_application_messages" : [response]
        }    
    
    log_node_stage(node_name, "Completed — recording application result")
    structured_messages = [
        {"role": "system", "content": load_prompt("apply_for_job/structured_system.txt")},
        *messages[1:],
        response,
        {
            "role": "user",
            "content": (
                "Based on the browsing evidence above, you MUST return the structured output evaluation now."
            ),
        },
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is recording the application result",
        structured_application_model.ainvoke(structured_messages),
    )
    logger.debug("Final response: %s", structured_response)

    if structured_response.requires_human:
        application_status = (
            "Needs CAPTCHA"
            if structured_response.human_intervention_reason == "CAPTCHA"
            else "In Progress"
        )
        next_step = "update_job_status"
    else:
        application_status = ""
        next_step = "build_verify_job_status_messages"
    
    return {
        "next_step": next_step,
        "is_applied": structured_response.is_applied,
        "url_applied_on": structured_response.url_applied_on,
        "final_status_message": structured_response.final_status_message,
        "is_duplicate": structured_response.is_duplicate,
        "requires_human": structured_response.requires_human,
        "human_intervention_reason": structured_response.human_intervention_reason,
        "human_intervention_prompt": structured_response.human_intervention_prompt,
        "human_intervention_origin": "submit_job_application",
        "application_status": application_status,
        "submit_job_application_messages" : [response]
    }


@trace_node
def build_verify_job_status_messages(state):
    return {
        "verify_job_status_messages": [
            {"role": "system", "content": load_prompt("verify_application/system.txt")},
            {
                "role": "user",
                "content": load_prompt(
                    "verify_application/user.txt",
                    application_url=state.get("url_applied_on", ""),
                    is_applied=state.get("is_applied", False),
                ),
            },
        ]
    }


@trace_node
async def verify_job_status(state):
    node_name = "verify_job_status"
    log_node_stage(node_name, "Started — verifying job application success")

    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("application_verification_model")
    messages = state["verify_job_status_messages"]

    response = await await_with_progress(
        node_name,
        "Agent is verifying the application success",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        return {
            "verification_phase": "NEEDS_TOOLS",
            "tool_call_origin": "verify_job_status",
            "verify_job_status_messages": [response],
        }

    log_node_stage(node_name, "Completed — recording verification result")
    structured_messages = [
        {"role": "system", "content": load_prompt("verify_application/structured_system.txt")},
        *messages[1:],
        response,
        {
            "role": "user",
            "content": (
                "Based on the browsing evidence above, you MUST return the structured output evaluation now."
            ),
        },
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is recording the verification result",
        structured_model.ainvoke(structured_messages),
    )
    logger.debug("Verification result: %s", structured_response)

    return {
        "verification_phase": "APPLIED" if structured_response.application_status == "APPLIED" else "NOT_APPLIED",
        "verify_job_status_messages": [response, structured_response.message]
    }


@trace_node
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

    verified_status = state.get("application_status", "").strip()
    if verified_status:
        application_status = verified_status
    elif state.get("is_applied") or state.get("is_duplicate"):
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


@trace_node
async def human_in_the_loop(state):
    """Handles human intervention for CAPTCHA or other issues.

    Returns:
        dict: Next step in the workflow.
    """
    # Logging
    node_name = "human_in_the_loop"
    log_node_stage(node_name, "Started — human intervention required")
    
    user_input = interrupt({
        "prompt": f"{state['human_intervention_prompt']}",
        "reason": f"{state['human_intervention_reason']}"
    })
    
    return {
        "next_step": state['human_intervention_origin'],
        "requires_human": False
    }
