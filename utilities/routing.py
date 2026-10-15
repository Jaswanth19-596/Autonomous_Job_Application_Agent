"""LangGraph routing helpers."""

import logging
from typing import Any

from .diagnostics import log_workflow_event


logger = logging.getLogger(__name__)


def route_user_request(state: dict[str, Any]) -> str:
    """Route a user request to the next workflow node."""

    next_node = state.get("user_node_decision")

    destinations = {
        "NEEDS_TOOLS": "tool_node",
        "CHAT": "collect_user_request",
        "APPLY_FOR_JOBS": "dispatch_job",
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
    """Return a completed login to the node that encountered its gate, or update job status on failure."""
    if state.get("next_step") == "update_job_status":
        destination = "update_job_status"
    else:
        phase = state["login_phase"]
        if phase == "NEEDS_TOOLS":
            destination = "tool_node"
        elif phase == "SUCCESS":
            destination = state.get("login_return_node")
            if destination not in {"resolve_application_url", "submit_job_application"}:
                raise ValueError(f"Unsupported login return node: {destination}")
        elif phase == "REQUIRES_HUMAN":
            destination = "human_in_the_loop"
        elif phase == "FAILED":
            destination = "human_in_the_loop" if state.get("requires_human") else "update_job_status"
        else:
            raise ValueError(f"Unsupported login phase: {phase}")
    log_workflow_event("route perform_login", phase=state.get("login_phase"), destination=destination)
    return destination


def route_after_dispatch(state: dict[str, Any]) -> str:
    """Start validation for the current queued job, or return to chat when exhausted."""
    destination = "is_job_valid" if state.get("job_url") is not None else "collect_user_request"
    log_workflow_event(
        "route dispatch_job",
        has_next_job=state.get("has_next_job", False),
        remaining_jobs=len(state.get("application_queue", [])),
        destination=destination,
    )
    return destination

def route_after_job_status_update(state: dict[str, Any]) -> str:
    """Continue a batch sequentially after persisting the current job's outcome."""
    destination = "dispatch_job" if state.get("application_queue") else "END"
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
    elif state["verification_phase"] == "FAILED":
        destination = "verify_job_status"
    else:
        raise ValueError(f"Unsupported verification phase: {state['verification_phase']}")
    log_workflow_event(
        "route verify_job_status",
        phase=state["verification_phase"],
        destination=destination,
    )
    return destination



def submit_application_decision(state: dict[str, Any]) -> str:
    if state["job_application_phase"] == "NOT_APPLIED":
        return "update_job_status"
    elif state["job_application_phase"] == "NEEDS_TOOLS":
        return "tool_node"
    elif state["job_application_phase"] == "REQUIRES_LOGIN":
        return "perform_login"
    elif state["job_application_phase"] == "APPLIED":
        return "verify_job_status"
    elif state["job_application_phase"] == "REQUIRES_HUMAN":
        return "human_in_the_loop"
    else:
        raise ValueError(f"Unsupported job_application_phase: {state['job_application_phase']}")
    
