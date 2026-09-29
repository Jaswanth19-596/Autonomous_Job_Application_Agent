"""Nodes for validation, extraction, persistence, and resume preparation."""

from utilities.jev import JevTokenLimitExceededException
import asyncio
import json
import logging
import random
from pathlib import Path

import pandas as pd

from jobs.fetcher import JOB_COLUMNS
from node_shared import (
    get_custom_model_with_tools,
    get_custom_structured_model,
    mcp_manager,
    visible_response_text,
)
from prompts import load_prompt
from utilities import (
    await_with_progress,
    compile_resume_pdf,
    log_node_stage,
    resume_latex_source,
    safe_resume_filename_part,
    trace_node,
)
from workbook.job_identity import canonical_job_id, canonical_job_url
from workbook.jobs_workbook import find_job_rows_by_canonical_url, upsert_job_rows
from workbook.pending_jobs import remove_pending_job_url
from utilities.jev import use_jev
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

logger = logging.getLogger(__name__)


@trace_node
async def is_job_valid(state):
    # Logging
    node_name = "is_job_valid"
    log_node_stage(node_name, "Started — connecting to browser tools")

    # Check if job URL already exists in applied_jobs.xlsx
    job_url = str(state.get("job_url") or state.get("actual_job_url") or "").strip()
    applied_jobs_path_str = state.get("applied_jobs_path")
    applied_jobs_path = Path(applied_jobs_path_str) if applied_jobs_path_str else Path(__file__).resolve().parent / "data/applied_jobs.xlsx"

    if job_url and applied_jobs_path.exists():
        existing_rows = find_job_rows_by_canonical_url(applied_jobs_path, job_url)
        if existing_rows:
            log_node_stage(node_name, "Completed — job URL already exists in applied_jobs.xlsx")
            return {
                "validation_phase": "NOT_ELIGIBLE",
                "final_status_message": "Job URL already exists in applied_jobs.xlsx",
                "is_applied": False,
            }

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

    log_node_stage(node_name, "Jev is deciding if the job is valid or not")

    jev_output = None

    while(True):
        try:
            jev_output = use_jev(   
            json.dumps({
                # Prompts are dicts, while model/tool replies are LangChain messages.
                "conversation": [
                    {"role": message.type, "content": message.content}
                    for message in [*messages, response]
                ],
            }),
            questions = {
                "valid_job": {
                    "type": "choice",
                    "instructions": """Role: You evaluate job listing validation results into a structured decision.

                    # Goal
                    Review the browser inspection actions, page contents, and findings in the conversation history to evaluate the job's eligibility. Your strong default is to APPROVE the job. Only reject if the evidence is clear and undeniable. """,
                    "criteria": {
                        "ELIGIBLE": "The job is eligible for application.",
                        "NOT_ELIGIBLE": "The job is not eligible for application."
                    }
                },
                "invalid_job_reason": {
                    "type": "choice",
                    "instructions": """Role: You choose one reason from the following options that best explains why the job is not eligible.""",
                    "criteria": {
                        "US_CITIZENSHIP_REQUIRED": "U.S. citizenship is explicitly required (general U.S. work authorization is NOT a disqualifier).",
                        "OPT_STEM_NOT_SUPPORTED": "The employer explicitly states that OPT/STEM OPT cannot be supported.",
                        "ROLE_OUTSIDE_US": "The role is explicitly outside the United States with no U.S. location or U.S.-remote option.",
                        "NEEDS_SECURITY_CLERANCE": "The job explicitly requires a security clearance (e.g., 'Public Trust', 'Secret', 'TS/SCI') that you do not possess.",
                        "DOESNT_MEET_REQUIREMENTS": "The job requirements are not met with respect to your profile",
                        "BLOCKED_BY_LOGIN": "The job is blocked by a login page and you are not able to access the job details",
                        "JOB_CLOSED_OR_EXPIRED": "The job is closed or expired",
                        "NOT_A_JOB_POSTING": "The page is not a job posting",
                        "ALREADY_APPLIED": "The job is already applied",
                        "PAGE_IN_FOREIGN_LANGUAGE": "The job posting is in a foreign language",
                        "OTHER": "Other reason"
                    }
                }
            }
            )
            break
        except JevTokenLimitExceededException as e:
            logger.error("Token limit exceeded for job validation, Trimming messages for next attempt")
            
            trimmed_messages = []
            for i in range(2,-4):
                trimmed_messages.append(messages[i])

            messages = trimmed_messages
            
    return {
        "validation_phase": jev_output['valid_job']['choice'],
        "final_status_message": jev_output['invalid_job_reason']['choice'],
        "is_applied": False,
    }





async def _get_page_text_via_chrome(job_url: str) -> str:
    """Navigate the shared Chrome browser to *job_url* and return its body text.

    Uses the Chrome DevTools MCP tools already managed by ``mcp_manager`` so
    that ``extract_job_details`` no longer needs to spin up a separate
    Playwright browser.
    """
    import re as _re

    chrome_tools = await mcp_manager.start(["chrome"])
    by_name = {t.name: t for t in chrome_tools}

    # --- helpers --------------------------------------------------------
    def _first_text(result: object) -> str:
        if isinstance(result, list) and result:
            item = result[0]
            return item.get("text", "") if isinstance(item, dict) else str(item)
        if isinstance(result, dict):
            return result.get("text", "")
        return str(result)

    def _parse_page_id(raw: str) -> int | None:
        m = _re.search(r"(\d+):\s*\S+\s*\[selected\]", raw)
        if m:
            return int(m.group(1))
        m = _re.search(r"(\d+):\s*\S+", raw)
        return int(m.group(1)) if m else None

    def _unwrap_eval(raw: str) -> str:
        m = _re.search(r"```(?:json)?\s*(.*?)\s*```", raw, _re.DOTALL)
        if m:
            try:
                import json as _json
                val = _json.loads(m.group(1))
                return val if isinstance(val, str) else _json.dumps(val)
            except Exception:
                return m.group(1)
        return raw

    # --- 1. find or open a page ----------------------------------------
    page_id = None
    if "list_pages" in by_name:
        pages_raw = _first_text(await by_name["list_pages"].ainvoke({}))
        page_id = _parse_page_id(pages_raw)

    if page_id is not None and "navigate_page" in by_name:
        await by_name["navigate_page"].ainvoke(
            {"pageId": page_id, "type": "url", "url": job_url}
        )
    elif "new_page" in by_name:
        new_raw = _first_text(await by_name["new_page"].ainvoke({"url": job_url}))
        page_id = _parse_page_id(new_raw)
    else:
        raise RuntimeError("Chrome MCP tools missing navigate_page and new_page")

    if page_id is None:
        raise RuntimeError("Could not determine Chrome page ID for job URL")

    # --- 2. wait briefly for JS-heavy pages ----------------------------
    await asyncio.sleep(2)

    # --- 3. extract body text ------------------------------------------
    page_text = ""
    if "evaluate_script" in by_name:
        eval_raw = _first_text(
            await by_name["evaluate_script"].ainvoke(
                {
                    "pageId": page_id,
                    "function": "() => document.body.innerText",
                    "waitForStableDom": True,
                }
            )
        )
        page_text = _unwrap_eval(eval_raw)

    # Fallback to accessibility snapshot if evaluate_script failed.
    if not page_text or page_text.startswith("Error"):
        if "take_snapshot" in by_name:
            page_text = _first_text(
                await by_name["take_snapshot"].ainvoke({"pageId": page_id})
            )

    return page_text


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

    # Use the shared Chrome browser via MCP tools instead of launching a
    # separate Playwright browser, so the session (cookies, tabs, etc.)
    # stays consistent with every other node in the workflow.
    page_text = await _get_page_text_via_chrome(job_url)

    messages = [
        SystemMessage(content = load_prompt("fetch_job_description/system.txt")),
        HumanMessage(content = load_prompt(
            "fetch_job_description/user.txt",
            job_url=job_url,
            page_text=page_text,
        ))
    ]

    job_details = await await_with_progress(
        node_name,
        "Agent is extracting job details",
        structured_model.ainvoke(messages),
    )

    log_node_stage(node_name, "Completed — job details extracted")
    logger.debug("Extracted job details: %s", job_details)

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

    # Older checkpoints predate the experiences state field. Recover the briefs
    # from disk instead of silently omitting them when resuming customization.
    experiences = state.get("experiences")
    if not experiences:
        experiences_dir = Path(__file__).resolve().parent / "user/experience"
        experiences = "\n\n".join(
            f"# {path.relative_to(experiences_dir)}\n\n{path.read_text(encoding='utf-8')}"
            for path in sorted(experiences_dir.rglob("*.md")) if path.is_file()
        )

    system_prompt = load_prompt("resume_customizer/system.txt")
    user_prompt = load_prompt(
        "resume_customizer/user.txt",
        descriptionText=job_details.get("descriptionText") or "",
        resume_text = resume_data,
        projects=projects,
        experiences=experiences,
        title=job_details.get("title") or "",
        companyName=job_details.get("companyName") or "",
        reference_resume_tex=reference_resume_path.read_text(encoding="utf-8"),
    )

    model_with_tools = await get_custom_model_with_tools([])

    response = await model_with_tools.ainvoke([
        SystemMessage(content = system_prompt),
        HumanMessage(content = user_prompt)
    ])

    # Save the generated source and compile the matching PDF beside it.
    company = safe_resume_filename_part(job_details.get("companyName") or "Resume")
    title = safe_resume_filename_part(job_details.get("title") or f"Job_{random.randint(1000, 9999)}")
    tex_path = Path("resumes") / f"{company}_{title}/jaswanth_resume.tex"
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
            SystemMessage(content = load_prompt("apply_for_job/system.txt")),
            HumanMessage(content= load_prompt(
                "apply_for_job/user.txt",
                user_profile=json.dumps(state.get("user_profile")),
                resume_data=state.get("resume_data"),
                resume_file_path=str(pdf_path),
                job_details=json.dumps(job_details, default=str),
                customized_resume=latex_source,
                projects=json.dumps(projects, default=str),
            ))
        ],
        "resolve_application_url_messages": [
            SystemMessage(content = load_prompt("resolve_application_url/system.txt")),
            HumanMessage(content = load_prompt(
                    "resolve_application_url/user.txt",
                    job_url=state['job_url'],
                ),
            ),
        ],
        "perform_login_messages": [
            SystemMessage(content = load_prompt("perform_login/system.txt")),
        ]
    }
