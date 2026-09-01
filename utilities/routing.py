"""LangGraph routing helpers."""

import logging
from typing import Any

from .diagnostics import log_workflow_event


logger = logging.getLogger(__name__)


def route_user_request(state: dict[str, Any]) -> str:
    """Route a user request to the next workflow node."""
    # ``user_request_phase`` is the graph state field. Keep accepting the
    # original structured-output names so routing remains testable in isolation.
    phase = (
        state.get("user_request_phase")
        or state.get("next_step")
        or state.get("next_action")
    )
    destinations = {
        "NEEDS_TOOLS": "tool_node",
        "CHAT": "collect_user_request",
        "FETCH_JOB_DESCRIPTION": "extract_job_details",
        "FETCH_JOBRIGHT_JOBS": "fetch_jobright_jobs",
        "DISPATCH_JOBS": "prepare_job_application_queue",
        "END": "END",
    }
    try:
        destination = destinations[phase]
    except KeyError as exc:
        raise ValueError(f"Unsupported user-request phase: {phase}") from exc

    log_workflow_event(
        "route user_request_llm_node",
        phase=phase,
        destination=destination,
    )
    return destination


def route_application_access(state: dict[str, Any]) -> str:
    """Route based on whether the application site requires authentication."""
    destination = (
        "authenticate_with_job_site"
        if state["requires_login"]
        else "submit_job_application"
    )
    log_workflow_event(
        "route intermediate_node_for_submit_job_application",
        requires_login=state["requires_login"],
        destination=destination,
    )
    return destination


def route_after_validation(state: dict[str, Any]) -> str:
    if state["validation_phase"] == "ELIGIBLE":
        destination = "build_application_url_messages"
    elif state["validation_phase"] == "NOT_ELIGIBLE":
        destination = "update_job_status"
    elif state["validation_phase"] == "NEEDS_TOOLS":
        destination = "tool_node"
    else:
        raise ValueError(f"Unsupported validation phase: {state['validation_phase']}")
    log_workflow_event(
        "route validate_job_listing",
        phase=state["validation_phase"],
        destination=destination,
    )
    return destination


def route_after_dispatch(state: dict[str, Any]) -> str:
    """Start validation for the current queued job, or return to chat when exhausted."""
    destination = "build_validation_messages" if state.get("has_next_job") else "collect_user_request"
    log_workflow_event(
        "route dispatch_next_job",
        has_next_job=state.get("has_next_job", False),
        remaining_jobs=len(state.get("application_queue", [])),
        destination=destination,
    )
    return destination


def route_after_job_status_update(state: dict[str, Any]) -> str:
    """Continue a batch sequentially after persisting the current job's outcome."""
    destination = "dispatch_next_job" if state.get("application_queue") else "collect_user_request"
    log_workflow_event(
        "route update_job_status",
        remaining_jobs=len(state.get("application_queue", [])),
        destination=destination,
    )
    return destination

def route_after_verification(state: dict[str, Any]) -> str:
    if state["verification_phase"] == "APPLIED":
        destination = "update_job_status"
    elif state["verification_phase"] == "NOT_APPLIED":
        destination = "update_job_status"
    elif state["verification_phase"] == "NEEDS_TOOLS":
        destination = "tool_node"
    else:
        raise ValueError(f"Unsupported verification phase: {state['verification_phase']}")
    log_workflow_event(
        "route verify_job_status",
        phase=state["verification_phase"],
        destination=destination,
    )
    return destination



def submit_application_decision(state: dict[str, Any]) -> str:
    if state["requires_human"]:
        return "human_intervention"
    if state["is_applied"]:
        return "update_job_status"
    else:
        return "END"
    

def conditional_edge_submit_job_application(state) -> str:
    if state["requires_human"]:
        destination = "human_in_the_loop"
    else:
        # submit_job_application has already decided the normal next step. Route
        # through that decision so unsuccessful applications still update status.
        destination = state["next_step"]

    log_workflow_event(
        "route submit_job_application",
        requires_human=state["requires_human"],
        destination=destination,
    )
    return destination
