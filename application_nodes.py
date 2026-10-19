"""Nodes for application URL resolution, login, submission, and completion."""

import asyncio
import json
import logging
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
    trace_node,
)
from utilities.profile_memory import load_profile, question_context, recalled_answer, save_answer
from utilities.jev import JevTokenLimitExceededException, use_jev
from workbook.invalid_jobs import move_failed_job
from langchain_core.messages import SystemMessage, HumanMessage


logger = logging.getLogger(__name__)


@trace_node
async def resolve_application_url(state):
    """Resolve the selected job's application URL without authenticating it."""
    node_name = "resolve_application_url"
    logger.info("RESOLVE_APPLICATION_URL.entered", extra = {"job_url": state.get("job_url"), "queue_len": len(state.get("application_queue", []) )})

    model_with_tools = await get_custom_model_with_tools(["chrome"])
    structured_model = get_custom_structured_model("application_url_resolution_model")
    logger.debug("RESOLVE_APPLICATION_URL.models.loaded", extra = {"job_url": state.get("job_url")})

    messages = state.get("resolve_application_url_messages")

    is_authenticated = state.get("is_authenticated", False)

    if messages is None :
        logger.error(f"RESOLVE_APPLICATION_URL.messages.missing", extra = {"job_url": state.get("job_url")})
        raise ValueError("resolve_application_url_messages is missing")
    
    if len(messages) == 0:
        logger.error(f"RESOLVE_APPLICATION_URL.messages.empty", extra = {"job_url": state.get("job_url")})
        raise ValueError("resolve_application_url_messages is empty")

    response = await await_with_progress(
        node_name,
        "Agent is running the browser to resolving the application URL",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        logger.info(f"RESOLVE_APPLICATION_URL.tool_call", extra={"tool_calls": response.tool_calls, "job_url": state.get("job_url")})
        return {
            "resolution_phase": "NEEDS_TOOLS",
            "tool_call_origin": "resolve_application_url",
            "resolve_application_url_messages": [response],
        }

    logger.info("RESOLVE_APPLICATION_URL.no_tool_calls", extra = {"job_url": state.get("job_url")})

    structured_messages = [
        SystemMessage(content=load_prompt("resolve_application_url/structured_system.txt")),
        *messages,
        response,
        HumanMessage(content="Based on the browsing evidence above, you MUST return the structured output now."),
    ]
    
    structured_response = None
    for attempt in range(4):
        logger.info(f"RESOLVE_APPLICATION_URL.structured_output.request", extra={"attempt": attempt, "job_url": state.get("job_url")})
        try:
            structured_response = await await_with_progress(
                node_name,
                "Agent is evaluating the resolved application URL",
                structured_model.ainvoke(structured_messages),
            )
        except ValidationError as error:
            logger.warning(f"RESOLVE_APPLICATION_URL.structured_output.validation_failed", extra={"error": error, "job_url": state.get("job_url")})
            if attempt == 3:
                logger.error(f"RESOLVE_APPLICATION_URL.structured_output.validation_failed.final_attempt", extra={"attempt": attempt, "job_url": state.get("job_url")})
                raise
            issues = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                for item in error.errors(include_input=False, include_url=False)
            )
            logger.error(f"RESOLVE_APPLICATION_URL.structured_output.validation_failed.issues", extra={"issues": issues, "job_url": state.get("job_url")})
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
        logger.error(f"RESOLVE_APPLICATION_URL.structured_output.missing", extra={"attempt": attempt, "job_url": state.get("job_url")})
        raise ValueError("Model didn't produce the structured output")

    logger.debug(f"RESOLVE_APPLICATION_URL.structured_output", extra={"structured_response": structured_response, "job_url": state.get("job_url")})

    result = structured_response.result

    if result.action == "REQUIRES_LOGIN":
        logger.info(f"RESOLVE_APPLICATION_URL.requires_login", extra={"login_page_url": result.login_page_url, "job_url": state.get("job_url")})
        return {
            "resolution_phase": "REQUIRES_LOGIN",
            "actual_job_url": result.login_page_url, 
            "login_return_node": "resolve_application_url",
            "perform_login_messages": [HumanMessage(content=f"The message from the previous node is: {result.login_message}\n")]
        }
    
    if result.action == "UNKNOWN_ISSUE":
        logger.info("RESOLVE_APPLICATION_URL.unknown_issue", extra={"issue_page_url": result.issue_page_url, "job_url": state.get("job_url")})
        return {
            "resolution_phase": "FAILED",
            "actual_job_url": result.issue_page_url,
            "requires_human": True,
            "human_intervention_reason": result.human_intervention_reason,
            "human_intervention_prompt": result.human_intervention_prompt,
            "human_intervention_origin": "resolve_application_url",
        }
    
    # When the action is success
    logger.info("RESOLVE_APPLICATION_URL.success", extra={"actual_job_url": result.actual_job_url})
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
    logger.info("PERFORM_LOGIN.started", extra={
        "actual_job_url": state.get("actual_job_url")
    })

    messages = state.get("perform_login_messages")
    
    if messages is None:
        logger.error("PERFORM_LOGIN.messages_missing", extra={
            "actual_job_url": state.get("actual_job_url")
        })
        raise ValueError("Messages are required for perform_login node")

    if len(messages) == 0:
        logger.error("PERFORM_LOGIN.messages_length_zero", extra={
            "actual_job_url": state.get("actual_job_url")
        })
        raise ValueError("Messages are required for perform_login node")


    model_with_tools = await get_custom_model_with_tools(["chrome", "gmail"])
    structured_model = get_custom_structured_model("authentication_model")

    response = await await_with_progress(
        node_name,
        "Agent is using browser to handle sign-in",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        logger.debug("PERFORM_LOGIN.tool_calls", extra={
            "tool_calls": response.tool_calls
        })
        return {
            "login_phase": "NEEDS_TOOLS",
            "tool_call_origin": "perform_login",
            "perform_login_messages": [response],
        }

    logger.info("PERFORM_LOGIN.completed_logging_in")
    structured_messages = [
        SystemMessage(content=load_prompt("perform_login/structured_system.txt")),
        *messages[1:],
        response,
        HumanMessage(content="Based on the browsing evidence above, you MUST return the structured output evaluation now."),
    ]
    structured_response = await await_with_progress(
        node_name,
        "Agent is evaluating next step",
        structured_model.ainvoke(structured_messages),
    )

    if structured_response.is_logged_in:
        logger.info("PERFORM_LOGIN.success", extra={
            "logged_in_url": structured_response.logged_in_url,
            "login_message": structured_response.message,
            "requires_human": structured_response.requires_human,
        })
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
    else:
        logger.info("PERFORM_LOGIN.requires_human", extra={
            "login_message": structured_response.message,
            "requires_human": structured_response.requires_human,
        })
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
    logger.info("SUBMIT_JOB_APPLICATION.started", extra={
        "job_url": state.get("actual_job_url")
    })
    
    profile = load_profile()
    messages = state['submit_job_application_messages']

    model_with_tools = await get_custom_model_with_tools(
        ["chrome", "gmail", "application_answers", "cover_letter"],
        application_answer_source=application_answer_source(state, profile),
    )
    structured_job_application_model = get_custom_structured_model("job_application_model")

    response = await await_with_progress(
        node_name,
        "Agent is filling the job application",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        logger.debug("SUBMIT_JOB_APPLICATION.tool_calls", extra={
            "tool_calls": response.tool_calls
        })
        return {
            "job_application_phase": "NEEDS_TOOLS",
            "tool_call_origin": "submit_job_application",
            "submit_job_application_messages": [response],
        }    
    
    logger.info("SUBMIT_JOB_APPLICATION.agent_decided_next_step")
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
            "Agent is evaluating the next step",
            structured_job_application_model.ainvoke(structured_messages),
        )
        
        if not structured_response:
            logger.warning("SUBMIT_JOB_APPLICATION.structured_response_not_found")
            structured_messages.append({
            "role": "user",
            "content": (
                "Please call the tool to produce structured output."
            ),
         })


        if structured_response:
            break

    logger.debug("SUBMIT_JOB_APPLICATION.structured_response", extra={
        "structured_response": structured_response
    })
    application_result = structured_response.decision

    if application_result.action == "APPLICATION_APPLIED":
        logger.info("SUBMIT_JOB_APPLICATION.application_applied", extra={
            "url_applied_on": application_result.url_applied_on,
        })
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
        logger.info("SUBMIT_JOB_APPLICATION.requires_login", extra={
            "logged_in_url": application_result.logged_in_url,
            "login_message": application_result.message,
            "requires_human": application_result.requires_human,
        })
        return {
            "job_application_phase": "REQUIRES_LOGIN",
            "login_return_node": "submit_job_application",
        }

    if application_result.action == "REQUIRES_HUMAN":
        logger.info("SUBMIT_JOB_APPLICATION.requires_human", extra={
            "human_intervention_reason": application_result.human_intervention_reason,
            "human_intervention_prompt": application_result.human_intervention_prompt,
            "human_intervention_origin": "submit_job_application",
        })
        return {
            "job_application_phase": "REQUIRES_HUMAN",
            "requires_human": True,
            "human_intervention_reason": application_result.human_intervention_reason,
            "human_intervention_prompt": application_result.human_intervention_prompt,
            "human_intervention_origin": "submit_job_application",
            "submit_job_application_messages": [response],
        }
    if application_result.action == "SKIP_JOB":
        logger.info("SUBMIT_JOB_APPLICATION.skip_job", extra={
            "reason": application_result.reason,
        })
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
    logger.info("VERIFY_JOB_STATUS.started", extra={
        "job_url": state.get("actual_job_url")
    })

    model_with_tools = await get_custom_model_with_tools(["chrome", "gmail"])
    structured_model = get_custom_structured_model("application_verification_model")
    messages = state["verify_job_status_messages"]

    response = await await_with_progress(
        node_name,
        "Agent is verifying the application success",
        model_with_tools.ainvoke(messages),
    )

    if response.tool_calls:
        logger.info("VERIFY_JOB_STATUS.tool_calls", extra={
            "tool_calls": response.tool_calls
        })
        return {
            "verification_phase": "NEEDS_TOOLS",
            "tool_call_origin": "verify_job_status",
            "verify_job_status_messages": [response],
        }

    logger.info("VERIFY_JOB_STATUS.agent_decided_next_step")
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
            "Agent is evaluating the next step",
            structured_model.ainvoke(structured_messages),
        )

        if not structured_response:
            logger.warning("VERIFY_JOB_STATUS.structured_response_not_found")
            structured_messages.append({
                "role": "user",
                "content": (
                    "Please call the tool to produce structured output."
                ),
            })

        if structured_response:
            break

    logger.debug("VERIFY_JOB_STATUS.structured_response", extra={
        "structured_response": structured_response
    })

    verification_decision = structured_response.decision
    is_applied = verification_decision.action == "Applied"
    logger.info("VERIFY_JOB_STATUS.verification_decision", extra={
        "is_applied": is_applied,
        "application_status": verification_decision.action,
        "reason_for_not_applying": verification_decision.reason_for_not_applied if not is_applied else "",
    })
    return {
        "verification_phase": (
            "APPLIED"
            if is_applied
            else "NOT_APPLIED"
        ),
        "application_status": verification_decision.action,
        "is_applied": is_applied,
        "reason_for_not_applying": (
            verification_decision.reason_for_not_applied
            if not is_applied
            else None
        ),
        "verify_job_status_messages": [response, structured_response],
        "messages": [{
            'role': 'user',
            "content": f"""The job {state.get('job_details',{}).get('title')}{state.get("job_url")} has been {verification_decision.action}. 
            And the status message is {verification_decision.evidence_source if is_applied else verification_decision.reason_for_not_applied}
            """
        }]
    }


from langgraph.types import Overwrite

@trace_node
async def update_job_status(state):
    """Persist the final application result and metrics in one workbook update."""
    from utilities.application_timing import finish_application_timing

    node_name = "update_job_status"
    logger.info("UPDATE_JOB_STATUS.started", extra={
        "job_url": state.get("actual_job_url")
    })
    pending_path = Path(state.get("pending_jobs_path") or Path(__file__).resolve().parent / "data/pending_jobs.xlsx")
    invalid_path = pending_path.with_name("invalid_jobs.xlsx")

    # If the job itself is invalid
    if state['validation_phase'] == "NOT_ELIGIBLE":
        logger.info("UPDATE_JOB_STATUS.job_is_not_eligible", extra={
            "job_url": state.get("job_url")
        })
        # Rejected jobs skip extraction and may retain a previous job's details.
        # Identify this listing directly from the URL selected by dispatch_job.
        move_failed_job(
            pending_path, invalid_path, state.get("job_url"),
            state.get("reason_for_not_applying"),
        )
        logger.info("UPDATE_JOB_STATUS.rejected_job_moved_from_pending_to_invalid", extra={
            "job_url": state.get("job_url")
        })
        return {
            "completed_jobs": [state["job_url"]],
            "application_status": "Not Applied",
            **finish_application_timing(state, "Not Applied"),
        }

    # If the job is valid, but applying is unsuccessful
    job_details = state.get("job_details")
    job_id = job_details.get("id")
    if not job_id:
        logger.error("UPDATE_JOB_STATUS.cannot_update_job_status_because_job_has_no_stable_id", extra={
            "job_url": state.get("job_url")
        })
        raise ValueError("Cannot update job status because the job has no stable ID.")

    application_status = state.get("application_status")
    logger.info("UPDATE_JOB_STATUS.job_id_application_status", extra={
        "job_id": job_id,
        "application_status": application_status
    })
    
    reason_for_not_applying = state.get("reason_for_not_applying")
    
    workbook_updates = {
        "application_status": application_status,
    }
    if reason_for_not_applying:
        workbook_updates["reason"] = reason_for_not_applying

    timing_update = finish_application_timing(
        state,
        application_status,
        workbook_path=(
            state.get("applied_jobs_path")
            or Path(__file__).resolve().parent / "data/applied_jobs.xlsx"
        ),
        workbook_updates=workbook_updates,
    )

    if application_status != "Applied":
        logger.info("UPDATE_JOB_STATUS.application_status_not_applied", extra={
            "job_url": state.get("job_url")
        })
        move_failed_job(pending_path, invalid_path, state.get("job_url"), reason_for_not_applying)

    logger.info("UPDATE_JOB_STATUS.completed", extra={
        "job_url": state.get("job_url")
    })
    return {
        **timing_update,
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

    origin = state["human_intervention_origin"]
    try:
        jev_response = await await_with_progress(
            node_name,
            "Jev is choosing the next step",
            asyncio.to_thread(
                use_jev,
                json.dumps({
                    "human_intervention_origin": origin,
                    "human_intervention_reason": state.get("human_intervention_reason"),
                    "human_intervention_prompt": state.get("human_intervention_prompt"),
                    "human_response": str(user_input),
                    "is_applied": state.get("is_applied", False),
                    "application_status": state.get("application_status"),
                }),
                questions={
                    "next_step": {
                        "type": "choice",
                        "instructions": (
                            "Choose the workflow outcome from the human's latest response. "
                            "Use the intervention prompt and origin only as context. Classify "
                            "the response by meaning, not by matching predefined words."
                        ),
                        "criteria": {
                            "RETRY_CURRENT_STEP": (
                                "The human supplied the requested answer, completed the manual "
                                "action, wants the interrupted step retried, or did not clearly "
                                "ask to stop processing this job."
                            ),
                            "MARK_NOT_APPLIED": (
                                "The human clearly wants to skip, cancel, abandon, or move past "
                                "this job, and does not say the application was submitted."
                            ),
                            "MARK_APPLIED": (
                                "The human clearly confirms that this job application was "
                                "successfully submitted or should be recorded as applied."
                            ),
                        },
                    },
                },
            ),
        )
        next_step = jev_response["next_step"]["choice"]
        if next_step not in {
            "RETRY_CURRENT_STEP",
            "MARK_NOT_APPLIED",
            "MARK_APPLIED",
        }:
            raise ValueError("Unexpected Jev human-intervention decision")
    except JevTokenLimitExceededException:
        logger.exception("Jev human-intervention classification exceeded its token limit")
        next_step = "RETRY_CURRENT_STEP"
    except Exception:
        logger.exception("Jev human-intervention classification failed")
        next_step = "RETRY_CURRENT_STEP"

    if next_step != "RETRY_CURRENT_STEP":
        applied = next_step == "MARK_APPLIED"
        return {
            "human_intervention_origin": "update_job_status",
            "application_status": "Applied" if applied else "Not Applied",
            "is_applied": applied,
            "reason_for_not_applying": (
                "" if applied else f"Human chose not to continue: {user_input}"
            ),
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            **_metrics_update,
        }

    if origin == "submit_job_application":
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

    elif origin == "perform_login":
        return {
            "perform_login_messages": [HumanMessage(content=user_input)],
            "requires_human": False,
            "human_intervention_reason": "",
            "human_intervention_prompt": "",
            **_metrics_update,
        }
    
    elif origin == "resolve_application_url":
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
