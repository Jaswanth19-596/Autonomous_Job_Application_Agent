

from collections import deque
import asyncio
from langgraph.types import interrupt
from mcp_connection import mcp_manager
# from langchain_openrouter import ChatOpenRouter  # Disabled: OpenRouter is no longer used.
from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig
import logging
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
load_dotenv()
from models.schemas import (
    ApplicationUrlResolution,
    AuthenticationResult,
    JobDetailsModel,
    ValidJobOrNotDecision,
    ApplicationResult,
    ApplicationVerificationResult,
    JobrightJob,
    UserRequestDecision
)
from prompts import load_prompt
from utilities.opencode import OPENCODE_USER_AGENT, opencode_session_headers
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
    persist_jobright_jobs,
    recent_workbook_job_summaries,
)
from playwright.async_api import async_playwright
import pandas as pd
from mcp_connection.mcp_manager import MCPManager
from langgraph.prebuilt import ToolNode
from langchain.messages import ToolMessage

# Legacy OpenRouter setup is deliberately disabled. Muse Spark is served by
# OpenCode Go's OpenAI-compatible Responses API, not Chat Completions.
# model = ChatOpenRouter(
#     model="deepseek/deepseek-v4-flash-0731",
#     api_key=os.getenv("OPENROUTER_API_KEY"),
#     openrouter_provider={...},
# )
model = ChatOpenAI(
    model="muse-spark-1.3-contributor",
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


async def get_custom_model_with_tools(tool_list, config: RunnableConfig | None = None):
    """Load requested MCP tools before binding them to the model."""
    headers = opencode_session_headers(config)
    mcp_servers = [tool_name for tool_name in tool_list if tool_name in {"chrome", "gmail"}]
    mcp_tools = await mcp_manager.start(mcp_servers) if mcp_servers else []

    for tool in mcp_tools:
        tools_by_name[tool.name] = tool

    tools = [*mcp_tools]
    if "custom" in tool_list:
        tools.append(get_jobs)
    return model.bind_tools(tools=tools, extra_headers=headers)

def _with_go_structured_output(schema, config: RunnableConfig | None = None):
    """Use Go-compatible tool output without forcing an unsupported named tool."""
    return model.with_structured_output(
        schema,
        method="function_calling",
        # Muse Spark's Responses API accepts only automatic tool selection.
        # The structured-output prompt still requires the returned schema tool.
        tool_choice="auto",
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
        return _with_go_structured_output(ApplicationUrlResolution, config)
    elif model_type == "authentication_model":
        return _with_go_structured_output(AuthenticationResult, config)
    elif model_type == "application_verification_model":
        return _with_go_structured_output(ApplicationVerificationResult, config)
    elif model_type == "jobright_job_model":
        return _with_go_structured_output(JobrightJob, config)
    else:
        return None
    



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
        "is_job_valid": "is_job_valid_messages",
        "resolve_application_url": "resolve_application_url_messages",
        "perform_login": "perform_login_messages",
        "verify_job_status": "verify_job_status_messages",
        "fetch_jobright_jobs": "jobright_collection_messages",
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
    
    if origin_node in {"fetch_jobright_jobs", "resolve_application_url", "perform_login"}:
        # These state fields use replacement semantics, so include the existing
        # transcript when appending the tool results.
        return {message_key: [*messages, *tool_messages]}
    return {message_key: tool_messages}
    


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
            "user_node_decision": "NEEDS_TOOLS",
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

    response_text = visible_response_text(response.content)
    if response_text:
        print(response_text)


    decision = structured_response.decision

    if decision.action == "CHAT":
        return {
            "messages": [response],
            "user_node_decision": decision.action,
        }


    if decision.action == "NEW_JOB_URL":
        return {
            "messages": [response],
            "user_node_decision": decision.action,
            "job_url": decision.job_url,
        }

    if decision.action == "FETCH_JOBRIGHT_JOBS":
        return {
            "messages": [response],
            "user_node_decision": decision.action,
            "jobright_job_count": decision.job_count,
            "jobright_jobs": [],
            "jobright_collection_messages": [],
            "jobright_collection_job_number": 1,
            "jobright_excluded_jobs": [],
        }

    if decision.action == "APPLY_FOR_JOBS":
        return {
            "messages": [response],
            "user_node_decision": decision.action,
            "job_query_filter": decision.job_query_filter.model_dump(),
        }


    logger.debug("Next action: %s", decision.action)


@trace_node
async def prepare_job_application_queue(state):

    job_query_filter = state["job_query_filter"]
    
    tool_args = (
        job_query_filter.model_dump()
        if hasattr(job_query_filter, "model_dump")
        else dict(job_query_filter)
    )
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
async def dispath_job(state):
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
        # Authentication and browser evidence are scoped to one queued job.
        # Carrying them into the next job can otherwise suppress a real gate or
        # revive a previous site's login transcript.
        "actual_job_url": "",
        "authenticated_session": False,
        "resolve_application_url_messages": [],
        "perform_login_messages": [],
        "application_queue": remaining_application_queue,
        "num_jobs_to_process": len(remaining_application_queue),
        "has_next_job": len(remaining_application_queue) > 0,
    }


@trace_node
async def fetch_jobright_jobs(state):
    """Advance one Jobright collection turn; ``tool_node`` executes all tools."""
    node_name = "fetch_jobright_jobs"
    job_count = state.get("jobright_job_count", 5)
    job_number = state.get("jobright_collection_job_number", 1)

    if not 1 <= job_count <= 5:
        raise ValueError("Jobright job count must be between 1 and 5.")
    if not 1 <= job_number <= job_count:
        raise ValueError("Jobright collection job number must be within the requested count.")

    workbook_path = Path(state["excel_file_path"])
    jobright_jobs = list(state.get("jobright_jobs", []))
    excluded_jobs = list(state.get("jobright_excluded_jobs", []))
    collection_messages = list(state.get("jobright_collection_messages", []))

    if not collection_messages:
        if not excluded_jobs:
            excluded_jobs = recent_workbook_job_summaries(workbook_path, limit=20)
        excluded_jobs_text = format_recent_workbook_jobs_for_prompt(excluded_jobs)
        collection_messages = [
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
        ]

    model_with_tools = await get_custom_model_with_tools(["chrome"])
    response = await await_with_progress(
        node_name,
        f"Collecting Jobright job {job_number}/{job_count}",
        model_with_tools.ainvoke(collection_messages),
    )
    collection_messages.append(response)
    if response.tool_calls:
        log_tool_calls(node_name, response.tool_calls)
        return {
            "jobright_collection_messages": collection_messages,
            "jobright_collection_job_number": job_number,
            "jobright_excluded_jobs": excluded_jobs,
            "jobright_jobs": jobright_jobs,
            "tool_call_origin": node_name,
            "next_step": "tool_node",
        }

    excluded_jobs_text = format_recent_workbook_jobs_for_prompt(excluded_jobs)
    structured_model = get_custom_structured_model("jobright_job_model")
    job = await await_with_progress(
        node_name,
        f"Structuring Jobright job {job_number}/{job_count}",
        structured_model.ainvoke([
            {
                "role": "system",
                "content": load_prompt(
                    "fetch_jobright_jobs/structured_system.txt",
                    job_number=job_number,
                    job_count=job_count,
                    excluded_jobs=excluded_jobs_text,
                ),
            },
            *collection_messages[1:],
            {
                "role": "user",
                "content": "Using only the browser evidence above, return one Jobright job in the required structured format.",
            },
        ]),
    )
    if job is None:
        raise RuntimeError("The browser agent did not return a Jobright job.")

    write_result = persist_jobright_jobs(workbook_path, [job])
    job_data = job.model_dump()
    jobright_jobs.append(job_data)
    excluded_jobs.append(
        {"companyName": job_data["companyName"], "title": job_data["title"]}
    )
    log_node_stage(
        node_name,
        f"Saved Jobright job {job_number}/{job_count} ({write_result.inserted} added, {write_result.updated} refreshed)",
    )

    if job_number < job_count:
        return {
            "jobright_collection_messages": [],
            "jobright_collection_job_number": job_number + 1,
            "jobright_excluded_jobs": excluded_jobs,
            "jobright_jobs": jobright_jobs,
            "next_step": "fetch_jobright_jobs",
        }

    message = (
        f"Saved {len(jobright_jobs)} Jobright job(s) to the workbook."
    )
    log_node_stage(node_name, f"Completed — {message}")
    return {
        "jobright_collection_messages": [],
        "jobright_collection_job_number": 1,
        "jobright_excluded_jobs": excluded_jobs,
        "jobright_jobs": jobright_jobs,
        "messages": [{"role": "assistant", "content": message}],
        "next_step": "collect_user_request",
    }





@trace_node
async def extract_job_details(state):
    # Logging
    node_name = "extract_job_details"
    log_node_stage(node_name, "Started — loading the job page")

    # Defining model
    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("job_details_model")

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
    if hasattr(job_details, "model_dump"):
        model_fields = set(type(job_details).model_fields)
        job_dict = job_details.model_dump(exclude_none=True)
    else:
        # Non-Pydantic values are supported for graph state restoration and
        # tests. Their supplied keys are the only fields eligible for writing.
        job_dict = dict(job_details)
        model_fields = set(job_dict)

    # Do not manufacture blank legacy fields: only fields defined by the
    # current JobDetails model and supported by the workbook schema are saved.
    row = {
        field: value
        for field, value in job_dict.items()
        if field in model_fields and field in JOB_COLUMNS
    }

    # ``id`` is workflow metadata, not extracted job detail. Use the supplied
    # URL only for identity generation; do not write it as ``link`` unless the
    # Pydantic model itself defines that field.
    identity_record = {
        **row,
        "link": job_dict.get("link") or state.get("job_url", ""),
        "applyUrl": row.get("applyUrl") or state.get("actual_job_url", ""),
    }
    row["id"] = canonical_job_id(identity_record)
    if not row["id"]:
        raise ValueError("Cannot save a user-supplied job without a stable job URL or ID.")
    row["application_status"] = "Not Applied"
    row["fetched_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
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
        "is_job_valid_messages" : [
            {'role': 'system', 'content': load_prompt("job_verifier/system.txt")},
            {'role':'user', 'content': load_prompt("job_verifier/user.txt", job_url = job_url)}
        ]
    }
        
    
@trace_node
async def is_job_valid(state):
    # Logging
    node_name = "is_job_valid"
    log_node_stage(node_name, "Started — connecting to browser tools")


    # Defining model with tools
    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("job_validation_model")

    messages = state["is_job_valid_messages"]

    response = await await_with_progress(
        node_name,
        "Agent is inspecting the job listing",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        return {
            "validation_phase": "NEEDS_TOOLS",
            "tool_call_origin": "is_job_valid",
            "is_job_valid_messages": [response]
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

    decision = structured_response.decision
    return {
        "validation_phase": "ELIGIBLE" if decision.valid_job == "YES" else "NOT_ELIGIBLE",
        "final_status_message": getattr(decision, "invalid_job_reason", ""),
        "is_applied": False,
    }

@trace_node
async def resolve_application_url(state):
    """Resolve the selected job's application URL without authenticating it."""
    node_name = "resolve_application_url"
    log_node_stage(node_name, "Started — resolving the application URL")

    saved_messages = state.get("resolve_application_url_messages")
    authenticated_session = state.get("authenticated_session", False)
    target_url = (
        state.get("actual_job_url")
        if authenticated_session and state.get("actual_job_url")
        else state.get("job_url", "")
    )
    initial_messages = [
        {"role": "system", "content": load_prompt("resolve_application_url/system.txt")},
        {
            "role": "user",
            "content": load_prompt(
                "resolve_application_url/user.txt",
                job_url=target_url,
            ),
        },
    ]
    # A completed login is authoritative state from the preceding node. Do not
    # reuse the transcript that captured the original login gate.
    messages = (
        [
            *initial_messages,
            {
                "role": "user",
                "content": (
                    "The dedicated login node has just verified an authenticated "
                    "session at this URL. Inspect the current page and determine "
                    "whether the application form is accessible. Do not set "
                    "requires_login=true unless fresh browser evidence proves a "
                    "different active authentication gate is blocking the form."
                ),
            },
        ]
        # perform_login clears this field once on success. Start one fresh,
        # authenticated resolver transcript at that point, but preserve it on
        # every following tool turn; rebuilding it each turn loses the tool
        # result and causes the model to call list_pages forever.
        if authenticated_session and not saved_messages
        else (saved_messages or initial_messages)
    )
    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("application_url_resolution_model")

    response = await await_with_progress(
        node_name,
        "Agent is resolving the application URL",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        # On the first browser turn these prompts only exist in this local
        # variable. Persist them with the assistant's tool request so the
        # next turn is a valid conversation: system, user, assistant(tool
        # call), tool result. Returning only ``response`` left the persisted
        # transcript starting with an assistant message, which the provider
        # correctly rejects as an illegal messages payload.
        transcript_update = [*messages, response]
        return {
            "resolution_phase": "NEEDS_TOOLS",
            "tool_call_origin": "resolve_application_url",
            "resolve_application_url_messages": transcript_update,
        }

    log_node_stage(node_name, "Completed — evaluating the application URL")
    structured_messages = [
        {"role": "system", "content": load_prompt("resolve_application_url/structured_system.txt")},
        *messages[1:],
        response,
        {
            "role": "user",
            "content": "Based on the browsing evidence above, you MUST return the structured output now.",
        },
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is evaluating the resolved application URL",
        structured_model.ainvoke(structured_messages),
    )

    retry = 3
    while structured_response is None and retry > 0:
        print("Re-running structured model for application URL resolution")
        structured_response = await await_with_progress(
            node_name,
            "Agent is evaluating the resolved application URL",
            structured_model.ainvoke(structured_messages),
        )
        retry -= 1

    if structured_response is None:
        raise ValueError("Model didn't produce the structured output")

    logger.debug("Final response: %s", structured_response)
    if structured_response.requires_login:
        if authenticated_session:
            # Never route a known-authenticated session back through the same
            # login node. A different fresh gate needs human review instead.
            return {
                "resolution_phase": "FAILED",
                "actual_job_url": structured_response.actual_job_url,
                "requires_human": True,
                "human_intervention_reason": "OTHER",
                "human_intervention_prompt": (
                    "The application still reports an authentication gate after "
                    "a successful sign-in. Review the current page manually."
                ),
                "human_intervention_origin": "resolve_application_url",
            }
        return {
            "resolution_phase": "REQUIRES_LOGIN",
            "actual_job_url": structured_response.actual_job_url,
            "login_return_node": "resolve_application_url",
        }

    return {
        "resolution_phase": "SUCCESS" if structured_response.ready_to_apply else "FAILED",
        "actual_job_url": structured_response.actual_job_url,
        "submit_job_application_messages": (
            [
                {
                    "role": "user",
                    "content": f"The Actual Job URL is : {structured_response.actual_job_url}\n",
                }
            ]
            if structured_response.ready_to_apply
            else []
        ),
    }


@trace_node
async def perform_login(state):
    """Authenticate the current application page, then return to its caller."""
    node_name = "perform_login"
    log_node_stage(node_name, "Started — signing in to the application site")

    saved_messages = state.get("perform_login_messages")
    target_url = state.get("actual_job_url") or state.get("job_url", "")
    messages = saved_messages or [
        {"role": "system", "content": load_prompt("perform_login/system.txt")},
        {
            "role": "user",
            "content": load_prompt("perform_login/user.txt", job_url=target_url),
        },
    ]
    model_with_tools = await get_custom_model_with_tools(["chrome", "gmail"])
    structured_model = get_custom_structured_model("authentication_model")

    response = await await_with_progress(
        node_name,
        "Agent is handling sign-in",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        transcript_update = [*messages, response]
        return {
            "login_phase": "NEEDS_TOOLS",
            "tool_call_origin": "perform_login",
            "perform_login_messages": transcript_update,
        }

    log_node_stage(node_name, "Completed — evaluating authentication")
    structured_messages = [
        {"role": "system", "content": load_prompt("perform_login/structured_system.txt")},
        *messages[1:],
        response,
        {
            "role": "user",
            "content": "Based on the browsing evidence above, you MUST return the structured output evaluation now.",
        },
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is evaluating authentication",
        structured_model.ainvoke(structured_messages),
    )
    logger.debug("Login result: %s", structured_response)

    if structured_response.is_logged_in:
        return {
            "login_phase": "SUCCESS",
            "actual_job_url": structured_response.logged_in_url,
            "authenticated_session": True,
            # The next resolver pass must use fresh authenticated evidence.
            "resolve_application_url_messages": [],
            "perform_login_messages": [],
            "requires_human": False,
            "human_intervention_reason": None,
            "human_intervention_prompt": "",
            "human_intervention_origin": "",
        }

    return {
        "login_phase": "FAILED",
        "actual_job_url": structured_response.logged_in_url,
        "requires_human": True,
        "human_intervention_reason": "OTHER",
        "human_intervention_prompt": structured_response.message or "Complete the sign-in on the current application page.",
        "human_intervention_origin": "perform_login",
    }

@trace_node
async def submit_job_application(state):
    node_name = "submit_job_application"
    log_node_stage(node_name, "Started — completing the job application")
    
    messages = state['submit_job_application_messages']

    model_with_tools = await get_custom_model_with_tools(["chrome", "gmail"])
    structured_job_application_model = get_custom_structured_model("job_application_model")

    response = await await_with_progress(
        node_name,
        "Agent is thinking",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        return {
            "job_application_phase": "NEEDS_TOOLS",
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
        structured_job_application_model.ainvoke(structured_messages),
    )
    logger.debug("Final response: %s", structured_response)

    
    application_result = structured_response.decision

    if application_result.is_applied:
        return {
            "job_application_phase": "APPLIED",
            "is_applied": True,
            "url_applied_on": application_result.url_applied_on,
            "final_status_message": application_result.final_status_message,
            "requires_human": False,
            "human_intervention_reason": None,
            "human_intervention_prompt": "",
            "human_intervention_origin": "",
            "submit_job_application_messages": [response],
        }

    if application_result.requires_login:
        return {
            "job_application_phase": "REQUIRES_LOGIN",
            "is_applied": False,
            "requires_human": False,
            "human_intervention_reason": None,
            "human_intervention_prompt": "",
            "human_intervention_origin": "",
            "login_return_node": "submit_job_application",
        }

    # The unsuccessful branch owns the human-intervention fields. It does not
    # provide confirmation metadata, so reset those fields rather than reading
    # attributes that are absent from ApplicationNotAppliedSubModel.
    return {
        "job_application_phase": "NOT_APPLIED",
        "is_applied": False,
        "url_applied_on": "",
        "final_status_message": "Application was not submitted.",
        "requires_human": application_result.requires_human,
        "human_intervention_reason": application_result.human_intervention_reason,
        "human_intervention_prompt": application_result.human_intervention_prompt or "",
        "human_intervention_origin": (
            "submit_job_application" if application_result.requires_human else ""
        ),
        "submit_job_application_messages": [response],
    }


@trace_node
def build_verify_job_status_messages(state):
    job_details = state.get("job_details") or state.get("current_job") or {}
    if hasattr(job_details, "model_dump"):
        job_details = job_details.model_dump()

    return {
        "verify_job_status_messages": [
            {"role": "system", "content": load_prompt("verify_application/system.txt")},
            {
                "role": "user",
                "content": load_prompt(
                    "verify_application/user.txt",
                    application_url=state.get("url_applied_on", ""),
                    is_applied=state.get("is_applied", False),
                    company_name=job_details.get("companyName", ""),
                    job_title=job_details.get("title", ""),
                ),
            },
        ]
    }


@trace_node
async def verify_job_status(state):
    node_name = "verify_job_status"
    log_node_stage(node_name, "Started — verifying job application success")

    model_with_tools = await get_custom_model_with_tools(["chrome", "gmail"])
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
                "Based on the page and Gmail evidence above, you MUST return the structured output evaluation now."
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
        "verification_phase": (
            "APPLIED"
            if structured_response.application_status == "Applied"
            else "NOT_APPLIED"
        ),
        "application_status": structured_response.application_status,
        "is_applied": structured_response.application_status == "Applied",
        "final_status_message": structured_response.message,
        "verify_job_status_messages": [response, structured_response.message],
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
    elif state.get("is_applied"):
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
        "requires_human": False,
        "human_intervention_reason": "",
        "human_intervention_prompt": "",
        "human_intervention_origin": "",
    }
