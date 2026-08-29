"""LangGraph routing helpers."""

import logging
from typing import Any


logger = logging.getLogger(__name__)


def route_user_request(state: dict[str, Any]) -> str:
    """Route a user request to the next workflow node."""
    if state["next_action"] == "CHAT":
        logger.debug("Routing to collect_user_request")
        return "collect_user_request"
    if state["next_action"] == "FETCH_JOB_DESCRIPTION":
        logger.debug("Routing to extract_job_details")
        return "extract_job_details"
    if state["next_action"] == "END":
        logger.debug("Routing to END")
        return "END"
    raise ValueError(f"Unsupported user-request action: {state['next_action']}")


def route_application_access(state: dict[str, Any]) -> str:
    """Route based on whether the application site requires authentication."""
    return (
        "authenticate_with_job_site"
        if state["requires_login"]
        else "submit_job_application"
    )
