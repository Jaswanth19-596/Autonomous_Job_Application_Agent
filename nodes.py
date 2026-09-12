

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
from utilities.application_answers import create_application_answer_tool
from utilities.form_fill import verified_fill
from utilities.profile_memory import (
    load_profile, save_answer, recalled_answer, question_context, applicable_profile,
)
from utilities import (
    await_with_progress,
    compile_resume_pdf,
    get_pending_or_invalid_jobs,
    get_applied_jobs,
    llm_logger_handler,
    log_node_stage,
    log_tool_calls,
    log_tool_result,
    log_workflow_event,
    resume_latex_source,
    safe_resume_filename_part,
    trace_node,
    update_job_status as update_job_status_tool,
)
from workbook.job_identity import canonical_job_id, canonical_job_url
from workbook.invalid_jobs import move_failed_job
from workbook.jobs_workbook import upsert_job_row, upsert_job_rows
from jobs.fetcher import APPLICATION_METRIC_COLUMNS, JOB_COLUMNS
from jobs.jobright import (
    JOBRIGHT_RECOMMENDED_URL,
    format_recent_workbook_jobright_urls_for_prompt,
    persist_jobright_jobs,
    recent_workbook_jobright_urls,
    remove_pending_job_url,
)
from playwright.async_api import async_playwright
import pandas as pd
from mcp_connection.mcp_manager import MCPManager
from langchain.messages import ToolMessage
import asyncio

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


# Questionnaire drafting uses Luna independently of the main workflow model.
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
        tools.extend((get_applied_jobs, get_pending_or_invalid_jobs))
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
        "job_details": state.get("job_details") or {},
        "user_profile": scoped_profile,
        "customized_resume": customized_resume,
        "application_answers": scoped_profile.get("application_answers", {}),
        "projects": state.get("projects") or "",
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
        except asyncio.TimeoutError:
            tool_result = {"error": f"Tool {tool_name} timed out after 60 seconds"}
            logger.exception("Tool %s timed out", tool_name)
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

    # Load every project brief. Keeping the filename with its content gives
    # downstream resume-customization prompts clear boundaries between projects.
    projects_dir = project_root / "user/projects"
    if not projects_dir.is_dir():
        raise FileNotFoundError(f"Projects directory not found: {projects_dir}")
    projects = "\n\n".join(
        f"# {project_file.relative_to(projects_dir)}\n\n"
        f"{project_file.read_text(encoding='utf-8')}"
        for project_file in sorted(path for path in projects_dir.rglob("*") if path.is_file())
    )

    # Resolve resume PDF path
    resume_file_path = project_root / "user/resume.pdf"
    if not resume_file_path.is_file():
        raise FileNotFoundError(f"Resume file not found: {resume_file_path}")

    reference_resume_path = project_root / "user/master_resume.tex"
    if not reference_resume_path.is_file():
        raise FileNotFoundError(
            f"Resume LaTeX template not found: {reference_resume_path}"
        )

    # Excel workbook path
    applied_jobs_path = str(project_root / "data/applied_jobs.xlsx")
    pending_jobs_path = str(project_root / "data/pending_jobs.xlsx")

    # System prompt for the chat node
    system_message = {"role": "system", "content": load_prompt("chat_with_user/system.txt")}

    log_node_stage(node_name, "Completed — user data loaded")

    return {
        "user_profile": user_profile,
        "resume_data": resume_data,
        "projects": projects,
        "resume_file_path": str(resume_file_path),
        "reference_resume_path": str(reference_resume_path),
        "applied_jobs_path": applied_jobs_path,
        "pending_jobs_path": pending_jobs_path,
        "messages": [system_message],
        "actual_job_url": "",
        "authenticated_session": False,
        "resolve_application_url_messages": [],
        "perform_login_messages": [],
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
async def understand_user_request_node(state):
    # Logging
    node_name = "understand_user_request_node"
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
            "tool_call_origin": "understand_user_request_node",
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
            "application_queue": decision.job_urls,
            "num_jobs_to_process": len(decision.job_urls)
        }

    logger.debug("Next action: %s", decision.action)


@trace_node
async def dispatch_job(state):
    application_queue = state.get("application_queue", [])
    if not application_queue:
        log_workflow_event("application queue exhausted", destination="collect_user_request")
        return {
            "current_job": None,
            "has_next_job": False,
            "num_jobs_to_process": 0,
        }

    job_url = application_queue[0]
    remaining_application_queue = application_queue[1:]
    
    log_workflow_event(
        "selected queued job for validation",
        job_url = job_url,
        remaining_jobs=len(remaining_application_queue),
    )

    return {
        "job_url": job_url,
        "application_recovery_attempts": 0,
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

    workbook_path = Path(state["pending_jobs_path"])
    jobright_jobs = list(state.get("jobright_jobs", []))
    excluded_jobs = list(state.get("jobright_excluded_jobs", []))
    collection_messages = list(state.get("jobright_collection_messages", []))

    if not collection_messages:
        if not excluded_jobs:
            excluded_jobs = recent_workbook_jobright_urls(workbook_path, limit=20)
        excluded_jobs_text = format_recent_workbook_jobright_urls_for_prompt(excluded_jobs)
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
        f"Collecting Jobright URL {job_number}/{job_count}",
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

    excluded_jobs_text = format_recent_workbook_jobright_urls_for_prompt(excluded_jobs)
    structured_model = get_custom_structured_model("jobright_job_model")
    job = await await_with_progress(
        node_name,
        f"Recording Jobright URL {job_number}/{job_count}",
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
    excluded_jobs.append(job_data["link"])
    log_node_stage(
        node_name,
        f"Saved Jobright URL {job_number}/{job_count} ({write_result.inserted} added, {write_result.updated} refreshed)",
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
        f"Saved {len(jobright_jobs)} Jobright URL(s) to the workbook."
    )
    log_node_stage(node_name, f"Completed — {message}")
    return {
        "jobright_collection_messages": [],
        "jobright_collection_job_number": 1,
        "jobright_excluded_jobs": excluded_jobs,
        "jobright_jobs": jobright_jobs,
        "messages": [{"role": "assistant", "content": [message, str(jobright_jobs)]}],
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
        browser = await p.chromium.launch(headless=False)

        page = await browser.new_page()

        await page.goto(
            job_url,
            wait_until="domcontentloaded",
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
        "job_details": job_details.model_dump()
    }


@trace_node
def save_job_to_workbook(state):
    # Logging
    node_name = "save_job_to_workbook"
    log_node_stage(node_name, "Started — preparing the spreadsheet update")

    # Getting the arguments
    job_details = state["job_details"]
    if hasattr(job_details, "model_dump"):
        job_details = job_details.model_dump(exclude_none=True)
    else:
        job_details = {
            field: value for field, value in dict(job_details).items() if value is not None
        }
    applied_jobs_path = state["applied_jobs_path"]

    # creating the excel row
    row = {
        field: value
        for field, value in job_details.items()
        if field in JOB_COLUMNS
    }

    # creating the identity record
    # Model output may contain the literal string "null". Persist the real
    # workflow URLs so placeholders cannot identify unrelated jobs as duplicates.
    for field, fallback in (("link", "job_url"), ("applyUrl", "actual_job_url")):
        row[field] = next(
            (str(value).strip() for value in (row.get(field), state.get(fallback))
             if canonical_job_url(value)),
            "",
        )
    identity_record = {key: value for key, value in row.items() if key != "id"}

    row["id"] = canonical_job_id(identity_record)

    if not row["id"]:
        raise ValueError("Could not generate a stable job ID.")

    row["application_status"] = "Not Applied"
    row["fetched_at"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    write_result = upsert_job_rows(
        Path(applied_jobs_path), [row]
    )
    action = "inserted" if write_result.inserted else "updated"

    # Remove the job from pending jobs list
    pending_url = str(state.get("job_url") or "").strip()
    removed_pending_jobs = 0
    if pending_url and state.get("pending_jobs_path"):
        removed_pending_jobs = remove_pending_job_url(
            Path(state["pending_jobs_path"]), pending_url,
        )

    # Logging
    log_node_stage(
        node_name,
        f"Completed — job {action} in applied jobs; removed {removed_pending_jobs} pending job(s)",
    )

    return {"job_details": row}


@trace_node
def build_validation_messages(state):

    job_url = state.get("job_url")
    log_workflow_event(
        "build validation messages",
        job_url=(state.get("job_url"))
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
        "final_status_message": decision.invalid_job_reason if decision.valid_job == "NO" else "",
        "is_applied": False,
    }


@trace_node
async def customize_resume(state):
    node_name = "customize_resume"
    log_node_stage(node_name, "Started — customizing resume")

    job_details = state.get("job_details")
    resume_data = state.get("resume_data")
    projects = state.get("projects")

    reference_resume_path = Path(
        state.get("reference_resume_path")
        or Path(__file__).resolve().parent / "user/master_resume.tex"
    )
    if not reference_resume_path.is_file():
        raise FileNotFoundError(
            f"Resume LaTeX template not found: {reference_resume_path}"
        )

    system_prompt = load_prompt("resume_customizer/system.txt")
    user_prompt = load_prompt(
        "resume_customizer/user.txt",
        job_description = job_details.get("description", ""),
        responsibilities = job_details.get("responsibilities", ""),
        required_skills = job_details.get("required_skills", ""),
        preferred_skills = job_details.get("preferred_skills", ""),
        resume_text = resume_data,
        projects=projects,
        job_title = job_details.get("title", ""),
        company = job_details.get("company", ""),
        reference_resume_tex=reference_resume_path.read_text(encoding="utf-8"),
    )

    model_with_tools = await get_custom_model_with_tools([])

    response = await model_with_tools.ainvoke([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt}
    ])

    # Save the generated source and compile the matching PDF beside it.
    company = safe_resume_filename_part(job_details.get("companyName", ""))
    title = safe_resume_filename_part(job_details.get("title", ""))
    tex_path = Path("resumes") / f"{company}_{title}.tex"
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    latex_source = resume_latex_source(visible_response_text(response.content))
    tex_path.write_text(latex_source, encoding="utf-8")

    pdf_path = await compile_resume_pdf(
        tex_path,
        latex_engine=state.get("latex_engine", "xelatex"),
    )

    return {
        "customized_resume_path": str(pdf_path),
        "customized_resume": latex_source,
        "submit_job_application_messages": [
            {'role': 'system', 'content': load_prompt("apply_for_job/system.txt")},
            {'role':'user', 'content': load_prompt(
                "apply_for_job/user.txt",
                user_profile=json.dumps(state.get("user_profile")),
                resume_data=state.get("resume_data"),
                resume_file_path=str(pdf_path),
                job_details=json.dumps(job_details, default=str),
                customized_resume=latex_source,
                projects=json.dumps(projects, default=str),
            )},
        ]
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
    
    profile = load_profile()
    messages = [*state['submit_job_application_messages'], {
        "role": "user",
        "content": "Latest saved profile and confirmed answers (use these before asking; "
                   "newer explicit answers supersede older facts for the same scope):\n" +
                   json.dumps(applicable_profile(profile, state), ensure_ascii=False),
    }]

    model_with_tools = await get_custom_model_with_tools(
        ["chrome", "gmail", "application_answers"],
        application_answer_source=application_answer_source(state, profile),
    )
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
            "submit_job_application_messages": [response],
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

    structured_response = ""

    for i in range(3):
        structured_response = await await_with_progress(
            node_name,
            "Agent is recording the application result",
            structured_job_application_model.ainvoke(structured_messages),
        )
        
        if not structured_response:
            structured_messages.append({
            "role": "user",
            "content": (
                "Please call the tool to produce structured output."
            ),
         })


        if structured_response:
            break

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

    reason = application_result.human_intervention_reason
    question = (application_result.human_intervention_prompt or "").strip()
    scope = application_result.question_scope
    known_answer = recalled_answer(profile, question, scope, question_context(state, scope))
    permitted_handoff = application_result.requires_human and (
        reason in {"CAPTCHA", "SECURITY"} or (reason == "QUESTION" and bool(question) and not known_answer)
    )
    if not permitted_handoff and state.get("application_recovery_attempts", 0) < 2:
        instruction = (
            f"Already answered: {question}\nUser's saved answer: {known_answer}\n"
            if known_answer else ""
        )
        return {
            "job_application_phase": "CONTINUE",
            "application_recovery_attempts": state.get("application_recovery_attempts", 0) + 1,
            "requires_human": False,
            "submit_job_application_messages": [response, {"role": "user", "content": instruction +
                "Continue completing and submitting this application yourself. Inspect the current page "
                "and use an alternative supported interaction for failed controls. Do not repeat a failed "
                "fill or ask me to fill, review, or submit the application. Ask only one missing factual "
                "question (QUESTION), or request the specific CAPTCHA/security action. If supported "
                "recovery methods are exhausted, report the technical failure with requires_human=false. "
                "Verify whether submission already occurred before taking any submit action."}],
        }

    # The unsuccessful branch owns the human-intervention fields. It does not
    # provide confirmation metadata, so reset those fields rather than reading
    # attributes that are absent from ApplicationNotAppliedSubModel.
    return {
        "job_application_phase": "NOT_APPLIED",
        "is_applied": False,
        "url_applied_on": "",
        "final_status_message": "Application was not submitted. " + (
            question if permitted_handoff else
            "Automatic recovery exhausted. " + visible_response_text(response.content)
        ),
        "requires_human": permitted_handoff,
        "human_intervention_reason": reason if permitted_handoff else None,
        "human_intervention_prompt": question if permitted_handoff else "",
        "question_scope": scope,
        "human_intervention_origin": (
            "submit_job_application" if permitted_handoff else ""
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

    structured_response = None
    for i in range(3):
        structured_response = await await_with_progress(
            node_name,
            "Agent is recording the verification result",
            structured_model.ainvoke(structured_messages),
        )

        if not structured_response:
            structured_messages.append({
                "role": "user",
                "content": (
                    "Please call the tool to produce structured output."
                ),
            })

        if structured_response:
            break

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
    pending_path = Path(state.get("pending_jobs_path") or Path(__file__).resolve().parent / "data/pending_jobs.xlsx")
    invalid_path = pending_path.with_name("invalid_jobs.xlsx")

    # If the job itself is invalid
    if state['validation_phase'] == "NOT_ELIGIBLE":
        # Rejected jobs skip extraction and may retain a previous job's details.
        # Identify this listing directly from the URL selected by dispatch_job.
        move_failed_job(
            pending_path, invalid_path, state.get("job_url"),
            state.get("final_status_message", ""),
        )
        log_node_stage(node_name, "Completed — rejected job moved from pending to invalid")
        return {"completed_jobs": [state["job_url"]], "application_status": "Not Applied"}

    # If the job is valid, but applying is unsuccessful
    job_details = state.get("job_details") or {}
    job_id = job_details.get("id")
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

    if application_status != "Applied":
        move_failed_job(pending_path, invalid_path, state.get("job_url"), final_status_message)

    log_node_stage(node_name, "Completed — job status updated")
    return {
        "completed_jobs": [state['job_url']],
        "application_status": application_status,
        "submit_job_application_messages": [],
        "is_job_valid_messages": [],
        "resolve_application_url_messages": [],
        "perform_login_messages": [],
        "verify_job_status_messages": []
    }


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
    
    if state["human_intervention_origin"] == "submit_job_application":
        profile = load_profile()
        if state.get("human_intervention_reason") == "QUESTION":
            scope = state.get("question_scope", "job")
            profile = save_answer(
                state["human_intervention_prompt"], user_input, scope,
                question_context(state, scope),
            )
        return {
            "user_profile": profile,
            "application_recovery_attempts": 0,
            "next_step": state['human_intervention_origin'],
            "submit_job_application_messages": [{"role": "user", "content": user_input}],
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            "human_intervention_origin": "",
        }

    elif state["human_intervention_origin"] == "perform_login":
        return {
            "next_step": state['human_intervention_origin'],
            "perform_login_messages": [{"role": "user", "content": user_input}],
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            "human_intervention_origin": "",
        }
    
    elif state["human_intervention_origin"] == "resolve_application_url":
        return {
            "next_step": state['human_intervention_origin'],
            "resolve_application_url_messages": [{"role": "user", "content": user_input}],
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            "human_intervention_origin": "",
        }

    else:
        return {
            "next_step": state['human_intervention_origin'],
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            "human_intervention_origin": "",
        }
