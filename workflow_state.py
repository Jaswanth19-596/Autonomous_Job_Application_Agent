"""State definition for the job-application workflow."""

from operator import add
from typing import Annotated, Literal

from langgraph.graph.message import MessagesState

from models.schemas import JobDetailsModel


def replace_messages(_existing: list[dict], replacement: list[dict]) -> list[dict]:
    """Replace a node-local browser transcript at a workflow boundary."""
    return replacement


class MyState(MessagesState):
    submit_job_application_messages: Annotated[list[dict], add] = []
    is_job_valid_messages: Annotated[list[dict], add] = []
    resolve_application_url_messages: Annotated[list[dict], add] = []
    perform_login_messages: Annotated[list[dict], add] = []
    verify_job_status_messages: Annotated[list[dict], add] = []

    user_profile: dict = {}
    resume_data: str = ""
    resume_file_path: str = ""
    current_state: str = ""

    #user request phase output
    job_url: str = ""
    user_node_decision: Literal["NEEDS_TOOLS", "CHAT", "APPLY_FOR_JOBS", "END"] = "CHAT"
    num_jobs_to_process: int = 0

    application_queue: list[str] = list()
    completed_jobs: Annotated[list[str], add] = []
    current_job: dict = None
    has_next_job: bool = False

    #customize resume phase
    job_details: JobDetailsModel = None
    customized_resume_path: str = ""
    customized_resume: str = ""
    projects: str = ""
    experiences: str = ""
    reference_resume_path: str = ""
    
    pending_jobs_path: str = ""
    applied_jobs_path: str = ""
    is_valid: bool = False
    actual_job_url: str = ""
    is_authenticated: bool = False
    application_status: str = ""
    application_messages: list[dict] = []
    tool_call_origin: str = ""
    next_step: str = ""

    # Human Review fields
    requires_human: bool = False
    human_intervention_reason: Literal["CAPTCHA", "QUESTION", "SECURITY", "OTHER"] | None = None
    question_scope: Literal["global", "employer", "job"] = "job"
    application_recovery_attempts: int = 0
    human_intervention_prompt: str | None = ""
    human_intervention_origin: str = ""
    human_response: str = ""

    # End-to-end wall time, including retries and human intervention.
    application_started_at: float | None = None
    application_elapsed_seconds: float | None = None

    # Application completed fields
    validation_phase : Literal["NEEDS_TOOLS", "ELIGIBLE", "NOT_ELIGIBLE", "END"] = "ELIGIBLE"
    verification_phase : Literal["NEEDS_TOOLS", "APPLIED", "NOT_APPLIED", "END"] = "NOT_APPLIED"
    resolution_phase : Literal["NEEDS_TOOLS", "REQUIRES_LOGIN", "SUCCESS", "FAILED"] = "FAILED"
    login_phase: Literal["NEEDS_TOOLS", "SUCCESS", "FAILED", "REQUIRES_HUMAN"] = "FAILED"
    login_return_node: Literal["resolve_application_url", "submit_job_application"] = None
    job_application_phase: Literal["NEEDS_TOOLS", "CONTINUE", "REQUIRES_LOGIN", "APPLIED", "NOT_APPLIED", "END"] = "NOT_APPLIED"

    is_applied: bool = False
    url_applied_on: str = ""
    final_status_message: str = ""
