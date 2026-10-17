"""Nodes for application URL resolution, login, submission, and completion."""

import logging
import re
from pathlib import Path

from langgraph.types import Overwrite, interrupt
from pydantic import ValidationError

from node_shared import (
    application_answer_source,
    get_custom_model_with_tools,
    get_custom_structured_model,
)
from prompts import load_prompt
from utilities import (
    await_with_progress,
    log_node_stage,
    log_tool_calls,
    log_tool_result,
    trace_node,
    update_job_status as update_job_status_tool,
)
from utilities.profile_memory import load_profile, question_context, recalled_answer, save_answer
from workbook.invalid_jobs import move_failed_job
from langchain_core.messages import SystemMessage, HumanMessage


logger = logging.getLogger(__name__)


@trace_node
async def resolve_application_url(state):
    """Resolve the selected job's application URL without authenticating it."""
    node_name = "resolve_application_url"
    log_node_stage(node_name, "Started — resolving the application URL")


    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("application_url_resolution_model")

    messages = state.get("resolve_application_url_messages")
    is_authenticated = state.get("is_authenticated", False)

    if messages is None:
        raise ValueError("resolve_application_url_messages is missing")

    response = await await_with_progress(
        node_name,
        "Agent is resolving the application URL",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        return {
            "resolution_phase": "NEEDS_TOOLS",
            "tool_call_origin": "resolve_application_url",
            "resolve_application_url_messages": [response],
        }

    log_node_stage(node_name, "Completed — evaluating the application URL")
    structured_messages = [
        SystemMessage(content=load_prompt("resolve_application_url/structured_system.txt")),
        *messages,
        response,
        HumanMessage(content="Based on the browsing evidence above, you MUST return the structured output now."),
    ]
    structured_response = None
    for attempt in range(4):
        try:
            structured_response = await await_with_progress(
                node_name,
                "Agent is evaluating the resolved application URL",
                structured_model.ainvoke(structured_messages),
            )
        except ValidationError as error:
            if attempt == 3:
                raise
            # Keep browsing evidence, and identify missing/invalid fields without
            # copying potentially sensitive input values into the repair prompt.
            issues = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in error.errors(include_input=False, include_url=False)
            )
            structured_messages = [*structured_messages, {
                "role": "user",
                "content": (
                    f"The structured output failed validation: {issues}. "
                    "Return a corrected result using the browsing evidence above. "
                    "REQUIRES_LOGIN requires login_page_url and login_message; "
                    "do not invent a URL or return the action alone."
                ),
            }]
            continue
        if structured_response is not None:
            break

    if structured_response is None:
        raise ValueError("Model didn't produce the structured output")

    logger.debug("Final response: %s", structured_response)

    result = structured_response.result

    if result.action == "REQUIRES_LOGIN":
        return {
            "resolution_phase": "REQUIRES_LOGIN",
            "actual_job_url": result.login_page_url, 
            "login_return_node": "resolve_application_url",
            "perform_login_messages": [HumanMessage(content=f"The message from the previous node is: {result.login_message}\n")]
        }
    
    if result.action == "UNKNOWN_ISSUE":
        return {
            "resolution_phase": "FAILED",
            "actual_job_url": result.issue_page_url,
            "requires_human": True,
            "human_intervention_reason": result.human_intervention_reason,
            "human_intervention_prompt": result.human_intervention_prompt,
            "human_intervention_origin": "resolve_application_url",
        }
    
    # When the action is success
    return {
        "resolution_phase": "SUCCESS",
        "actual_job_url": result.actual_job_url,
        "submit_job_application_messages": [
            {
                "role": "user",
                "content": f"The Actual Job URL is : {result.actual_job_url}\n",
            }
        ],
        "perform_login_messages": [
            {
                "role": "user",
                "content": load_prompt("perform_login/user.txt", job_url=result.actual_job_url or state['job_url'], user_profile = state['user_profile'])
            }
        ]
    }


@trace_node
async def perform_login(state):
    """Authenticate the current application page, then return to its caller."""
    node_name = "perform_login"
    log_node_stage(node_name, "Started — signing in to the application site")

    messages = state.get("perform_login_messages")
    
    model_with_tools = await get_custom_model_with_tools(["chrome", "gmail"])
    structured_model = get_custom_structured_model("authentication_model")

    response = await await_with_progress(
        node_name,
        "Agent is handling sign-in",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        
        return {
            "login_phase": "NEEDS_TOOLS",
            "tool_call_origin": "perform_login",
            "perform_login_messages": [response],
        }

    log_node_stage(node_name, "Completed — evaluating authentication")
    structured_messages = [
        SystemMessage(content=load_prompt("perform_login/structured_system.txt")),
        *messages[1:],
        response,
        HumanMessage(content="Based on the browsing evidence above, you MUST return the structured output evaluation now."),
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
            "is_authenticated": True,
            "requires_human": False,
            "resolve_application_url_messages": ([
                {
                "role": "user",
                "content": structured_response.message,
                }
            ] if state['login_return_node'] == 'resolve_application_url' else []),
            "submit_job_application_messages": ([{
                "role": "user",
                "content": structured_response.message,
                }
            ] if state['login_return_node'] == 'submit_job_application' else [])

            ,
            "perform_login_messages": [{
                "role": "user",
                "content": f"Login successfull {structured_response.logged_in_url}. Also I have updated the caller with the logged in url. "
                }
            ]
        }

    issue_msg = structured_response.message or "Login failed or requires human sign-in / verification."
    return {
        "login_phase": "REQUIRES_HUMAN",
        "is_authenticated": False,
        "requires_human": True,
        "human_intervention_reason": "OTHER",
        "human_intervention_prompt": f"Encountered a login issue: {issue_msg}. Would you like to complete sign-in manually, or continue with the next application?",
        "human_intervention_origin": "perform_login",
    }

@trace_node
async def submit_job_application(state):
    node_name = "submit_job_application"
    log_node_stage(node_name, "Started — completing the job application")
    
    profile = load_profile()
    messages = state['submit_job_application_messages']

    model_with_tools = await get_custom_model_with_tools(
        ["chrome", "gmail", "application_answers", "cover_letter"],
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
        SystemMessage(content=load_prompt("apply_for_job/structured_system.txt")),
        *messages[1:],
        response,
        HumanMessage(content="Based on the browsing evidence above, you MUST return the structured output evaluation now."),
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

    if application_result.action == "APPLICATION_APPLIED":
        # Stamp the applied-at timestamp in metrics
        from utilities.application_metrics import stamp_applied_at
        metrics = dict(state.get("application_metrics") or {})
        if metrics:
            stamp_applied_at(metrics)
        return {
            "job_application_phase": "APPLIED",
            "is_applied": True,
            "url_applied_on": application_result.url_applied_on,
            "requires_human": False,
            "human_intervention_reason": None,
            "human_intervention_prompt": "",
            "human_intervention_origin": "",
            "submit_job_application_messages": [response],
            "application_metrics": metrics,
            "verify_job_status_messages": [
                SystemMessage(content=load_prompt("verify_application/system.txt")),
                HumanMessage(
                    content=load_prompt(
                        "verify_application/user.txt",
                        application_url=application_result.url_applied_on,
                        is_applied=True,
                        company_name=state['job_details']['companyName'],
                        job_title=state['job_details']['title'],
                    ))
            ]
        }

    if application_result.action == "REQUIRES_LOGIN":
        return {
            "job_application_phase": "REQUIRES_LOGIN",
            "login_return_node": "submit_job_application",
        }

    if application_result.action == "REQUIRES_HUMAN":
        return {
            "job_application_phase": "REQUIRES_HUMAN",
            "requires_human": True,
            "human_intervention_reason": application_result.human_intervention_reason,
            "human_intervention_prompt": application_result.human_intervention_prompt,
            "human_intervention_origin": "submit_job_application",
            "submit_job_application_messages": [response],
        }
    if application_result.action == "SKIP_JOB":
        return {
            "job_application_phase": "NOT_APPLIED",
            "reason_for_not_applying": application_result.reason,
            "application_status": "Not Applied",
            "is_applied": False,
            "submit_job_application_messages": [response],
        }
    return {
        "job_application_phase": "NOT_APPLIED",
        "application_status": "Not Applied",
        "reason_for_not_applying": application_result.reason,
        "is_applied": False,
        "submit_job_application_messages": [response],
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
        SystemMessage(content=load_prompt("verify_application/structured_system.txt")),
        *messages[1:],
        response,
        HumanMessage(content="Based on the page and Gmail evidence above, you MUST return the structured output evaluation now."),
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
            if structured_response.verified_application_status == "Applied"
            else "NOT_APPLIED"
        ),
        "application_status": structured_response.verified_application_status,
        "is_applied": structured_response.verified_application_status == "Applied",
        "reason_for_not_applying": structured_response.message,
        "verify_job_status_messages": [response, structured_response.message],
        "messages": [{
            'role': 'user',
            "content": f"""The job {state.get('job_details',{}).get('title')}{state.get("job_url")} has been {structured_response.verified_application_status}. 
            And the status message is {structured_response.message}
            """
        }]
    }


from langgraph.types import Overwrite

@trace_node
async def update_job_status(state):
    """Persist the result of an application by directly invoking the status tool."""
    from utilities.application_timing import finish_application_timing

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
            state.get("reason_for_not_applying"),
        )
        log_node_stage(node_name, "Completed — rejected job moved from pending to invalid")
        return {
            "completed_jobs": [state["job_url"]],
            "application_status": "Not Applied",
            **finish_application_timing(state, "Not Applied"),
        }

    # If the job is valid, but applying is unsuccessful
    job_details = state.get("job_details")
    job_id = job_details.get("id")
    if not job_id:
        raise ValueError("Cannot update job status because the job has no stable ID.")

    application_status = state.get("application_status")

    reason_for_not_applying = state.get("reason_for_not_applying")
    tool_args = {
        "job_id": job_id,
        "application_status": application_status,
        "reason": reason_for_not_applying,
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
        move_failed_job(pending_path, invalid_path, state.get("job_url"), reason_for_not_applying)

    log_node_stage(node_name, "Completed — job status updated")
    return {
        **finish_application_timing(
            state, application_status,
            workbook_path=state.get("applied_jobs_path") or Path(__file__).resolve().parent / "data/applied_jobs.xlsx",
        ),
        "completed_jobs": [state['job_url']],
        "timed_out_questions": [],
        "application_status": application_status,
        "submit_job_application_messages": Overwrite([]),
        "is_job_valid_messages": Overwrite([]),
        "resolve_application_url_messages": Overwrite([]),
        "perform_login_messages": Overwrite([]),
        "verify_job_status_messages": Overwrite([]),
        "is_applied": False,
        "application_status": "Not Applied",
        "requires_login": False,
        "requires_human": False,
        "human_intervention_reason": "",
        "human_intervention_prompt": "",
        "verification_phase": "NOT_APPLIED",
        "verification_messages": Overwrite([]),
        "submit_job_application_messages": Overwrite([]),
        "is_job_valid_messages": Overwrite([]),
        "resolve_application_url_messages": Overwrite([]),
        "perform_login_messages": Overwrite([]),
        "verify_job_status_messages": Overwrite([]),
    }


@trace_node
async def human_in_the_loop(state):
    """Handles human intervention for CAPTCHA or other issues.

    Returns:
        dict: Next step in the workflow.
    """
    # Logging
    node_name = "human_in_the_loop"
    log_node_stage(node_name, "Started — human intervention")
    
    user_input = interrupt({
        "prompt": f"{state['human_intervention_prompt']}",
        "reason": f"{state['human_intervention_reason']}",
        "timeout_seconds": 60,
    })

    # Record human intervention in metrics
    from utilities.application_metrics import record_human_intervention
    metrics = dict(state.get("application_metrics") or {})
    if metrics:
        record_human_intervention(metrics)
    _metrics_update = {"application_metrics": metrics} if metrics else {}
    
    if isinstance(user_input, dict) and user_input.get("human_input_timed_out") is True:
        from utilities.human_input import timeout_update
        return {**timeout_update(state), **_metrics_update}

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
            "submit_job_application_messages": [HumanMessage(content=user_input)],
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            **_metrics_update,
        }

    elif state["human_intervention_origin"] == "perform_login":
        lower_input = str(user_input).strip().lower()
        skip_keywords = ["next", "skip", "continue", "move on", "pass", "cancel"]
        if any(kw in lower_input for kw in skip_keywords):
            return {
                "login_phase": "FAILED",
                "reason_for_not_applying": "Login failed",
                "application_status": "Not Applied",
                "requires_human": False,
                "human_intervention_reason": "",
                "human_intervention_prompt": "",
                "perform_login_messages": Overwrite([]),
                **_metrics_update,
            }
        return {
            "perform_login_messages": [HumanMessage(content=user_input)],
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            **_metrics_update,
        }
    
    elif state["human_intervention_origin"] == "resolve_application_url":
        lower_input = str(user_input).strip().lower()
        if re.search(r"\b(next|skip|continue|move on|pass|cancel)\b", lower_input):
            # Honor the user's explicit outcome before falling back to workflow
            # evidence. The default Not Applied value must not hide APPLIED.
            status_match = re.search(
                r"\bmark\b.*?\bas\s+(not applied|applied|failed|needs captcha|in progress)\b",
                lower_input,
            )
            if status_match:
                status = {
                    "not applied": "Not Applied", "applied": "Applied",
                    "failed": "Failed", "needs captcha": "Needs CAPTCHA",
                    "in progress": "In Progress",
                }[status_match.group(1)]
            elif state.get("is_applied") or state.get("job_application_phase") == "APPLIED":
                status = "Applied"
            else:
                status = state.get("application_status") or "Not Applied"
            return {
                "application_status": status,
                "is_applied": status == "Applied",
                "requires_human": False,
                "human_intervention_reason": "",
                "human_intervention_prompt": "",
                "resolve_application_url_messages": Overwrite([]),
                **_metrics_update,
            }
        return {
            "resolve_application_url_messages": [HumanMessage(content=user_input)],
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            **_metrics_update,
        }

    else:
        return {
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            **_metrics_update,
        }
