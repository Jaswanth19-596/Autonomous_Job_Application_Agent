"""Public compatibility exports for the workflow's node functions."""

import sys
from types import ModuleType

import application_nodes
import job_nodes
import node_shared
import user_nodes

from langgraph.types import Overwrite, interrupt

from application_nodes import (
    human_in_the_loop,
    perform_login,
    resolve_application_url,
    submit_job_application,
    update_job_status,
    verify_job_status,
)
from job_nodes import (
    customize_resume,
    extract_job_details,
    is_job_valid,
    save_job_to_workbook,
)
from node_shared import (
    application_answer_source,
    get_custom_model_with_tools,
    get_custom_structured_model,
    is_http_job_url,
    shutdown_mcp_tools,
    tool_node,
    visible_response_text,
)
from user_nodes import (
    collect_user_request,
    dispatch_job,
    initialize_user_data,
    understand_user_request_node,
)
from utilities import await_with_progress


__all__ = [
    "Overwrite",
    "application_answer_source",
    "await_with_progress",
    "collect_user_request",
    "customize_resume",
    "dispatch_job",
    "extract_job_details",
    "get_custom_model_with_tools",
    "get_custom_structured_model",
    "human_in_the_loop",
    "initialize_user_data",
    "interrupt",
    "is_http_job_url",
    "is_job_valid",
    "perform_login",
    "resolve_application_url",
    "save_job_to_workbook",
    "shutdown_mcp_tools",
    "submit_job_application",
    "tool_node",
    "understand_user_request_node",
    "update_job_status",
    "verify_job_status",
    "visible_response_text",
]


class _NodesModule(ModuleType):
    _forwarded_attributes = {
        "await_with_progress": (application_nodes, job_nodes, node_shared, user_nodes),
        "get_custom_model_with_tools": (application_nodes, job_nodes, user_nodes),
        "get_custom_structured_model": (application_nodes, job_nodes, user_nodes),
        "interrupt": (application_nodes, user_nodes),
    }

    def __setattr__(self, name, value):
        for module in self._forwarded_attributes.get(name, ()):
            setattr(module, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _NodesModule
