"""LangGraph routing helpers."""

import logging
from typing import Any

from .diagnostics import log_workflow_event


logger = logging.getLogger(__name__)


def route_user_request(state: dict[str, Any]) -> str:
    """Route a user request to the next workflow node."""

    next_node = (
        state.get("user_node_decision")
        or state.get("user_request_phase")
        or state.get("next_step")
        or state.get("next_action")
    )

    destinations = {
        "NEEDS_TOOLS": "tool_node",
        "CHAT": "collect_user_request",
        "NEW_JOB_URL": "build_validation_messages",
        "JOBRIGHT_JOBS": "fetch_jobright_jobs",
        "APPLY_FOR_JOBS": "prepare_job_application_queue",
        "END": "END",
    }
    try:
        destination = destinations[next_node]
    except KeyError as exc:
        raise ValueError(f"Unsupported user-request phase: {next_node}") from exc

    log_workflow_event(
        "route understand_user_request_node",
        phase=next_node,
        destination=destination,
    )
    return destination


def route_after_validation(state: dict[str, Any]) -> str:
    if state["validation_phase"] == "ELIGIBLE":
        destination = "extract_job_details"
    elif state["validation_phase"] == "NOT_ELIGIBLE":
        destination = "update_job_status"
    elif state["validation_phase"] == "NEEDS_TOOLS":
        destination = "tool_node"
    else:
        raise ValueError(f"Unsupported validation phase: {state['validation_phase']}")
    log_workflow_event(
        "route is_job_valid",
        phase=state["validation_phase"],
        destination=destination,
    )
    return destination


def route_after_resolving_url(state: dict[str, Any]) -> str:
    if state["resolution_phase"] == "NEEDS_TOOLS":
        destination = "tool_node"
    elif state["resolution_phase"] == "SUCCESS":
        destination = "submit_job_application"
    elif state["resolution_phase"] == "REQUIRES_LOGIN":
        destination = "perform_login"
    elif state["resolution_phase"] == "FAILED":
        destination = "human_in_the_loop"
    else:
        raise ValueError(f"Unsupported resolution phase: {state['resolution_phase']}")
    log_workflow_event(
        "route resolve_application_url",
        phase=state["resolution_phase"],
        destination=destination,
    )
    return destination


def route_after_login(state: dict[str, Any]) -> str:
    """Return a completed login to the node that encountered its gate."""
    phase = state["login_phase"]
    if phase == "NEEDS_TOOLS":
        destination = "tool_node"
    elif phase == "SUCCESS":
        destination = state.get("login_return_node", "")
        if destination not in {"resolve_application_url", "submit_job_application"}:
            raise ValueError(f"Unsupported login return node: {destination}")
    elif phase == "FAILED":
        destination = "human_in_the_loop"
    else:
        raise ValueError(f"Unsupported login phase: {phase}")
    log_workflow_event("route perform_login", phase=phase, destination=destination)
    return destination


def route_after_dispatch(state: dict[str, Any]) -> str:
    """Start validation for the current queued job, or return to chat when exhausted."""
    destination = "build_validation_messages" if state.get("current_job") is not None else "collect_user_request"
    log_workflow_event(
        "route dispath_job",
        has_next_job=state.get("has_next_job", False),
        remaining_jobs=len(state.get("application_queue", [])),
        destination=destination,
    )
    return destination


def route_after_jobright_collection(state: dict[str, Any]) -> str:
    """Continue Jobright collection through the shared tool execution node."""
    destination = state.get("next_step", "collect_user_request")
    if destination not in {"tool_node", "fetch_jobright_jobs", "collect_user_request"}:
        raise ValueError(f"Unsupported Jobright collection destination: {destination}")
    log_workflow_event(
        "route fetch_jobright_jobs",
        destination=destination,
        job_number=state.get("jobright_collection_job_number", 1),
    )
    return destination


def route_after_job_status_update(state: dict[str, Any]) -> str:
    """Continue a batch sequentially after persisting the current job's outcome."""
    destination = "dispath_job" if state.get("application_queue") else "collect_user_request"
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
    if state["job_application_phase"] == "NEEDS_TOOLS":
        return "tool_node"
    elif state["job_application_phase"] == "REQUIRES_LOGIN":
        return "perform_login"
    elif state["job_application_phase"] == "APPLIED":
        return "build_verify_job_status_messages"
    elif state["job_application_phase"] == "NOT_APPLIED":
        return "human_in_the_loop" if state["requires_human"] else "update_job_status"
    else:
        raise ValueError(f"Unsupported job_application_phase: {state['job_application_phase']}")
    

# def submit_application_decision(state) -> str:
#     if state["requires_human"]:
#         destination = "human_in_the_loop"
#     else:
#         # submit_job_application has already decided the normal next step. Route
#         # through that decision so unsuccessful applications still update status.
#         destination = state["next_step"]

#     log_workflow_event(
#         "route submit_job_application",
#         requires_human=state["requires_human"],
#         destination=destination,
#     )
#     return destination
