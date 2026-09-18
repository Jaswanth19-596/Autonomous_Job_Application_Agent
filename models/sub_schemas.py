from pydantic import BaseModel, Field
from typing import Literal, Optional

class JobQueryFilter(BaseModel):
    query: str = Field(description="The query to be used for searching jobs.")
    n: int = Field(ge = 1, le = 5, default = 5, description = "Number of jobs to fetch")
    sort_by: Literal["postedAt"] = "postedAt"
    order: Literal["desc", "asc"] = "desc"


class ChatWithUserSubModel(BaseModel):
    action: Literal["CHAT"]

class ApplyForJobsSubModel(BaseModel):
    action: Literal["APPLY_FOR_JOBS"]
    job_urls: list[str] = Field(description = "List of job URLs to apply for")


class ValidJobSubModel(BaseModel):
    valid_job: Literal["YES"]


class InvalidJobSubModel(BaseModel):
    valid_job: Literal["NO"]
    invalid_job_reason: str = Field(description="Reason for not applying if the job is not valid. Leave it blank if the job is valid.")




# URL Resolution
class ApplicationUrlResolutionIssueSubModel(BaseModel):
    requires_human: bool = Field(default=False, description="")
    human_intervention_prompt: str = Field(default="", description="")
    human_intervention_reason: Literal["CAPTCHA", "QUESTION", "SECURITY", "OTHER"] = Field(default=None, description="")
    action: Literal["UNKNOWN_ISSUE"] = "UNKNOWN_ISSUE"
    issue_page_url: str = Field(default="", description="URL of the page that requires human intervention.")

class ApplicationUrlReadySubModel(BaseModel):
    actual_job_url: str = Field(default="", description="")
    action: Literal["READY_TO_APPLY"] = "READY_TO_APPLY"

class ApplicationUrlLoginSubModel(BaseModel):
    action: Literal["REQUIRES_LOGIN"] = "REQUIRES_LOGIN"
    login_page_url: str = Field(description="URL of the page that requires login.")
    login_message: str = Field(description = "Message to be shown to the agent handling the login.")



# Submitting Job Application. 
class ApplicationAppliedSubModel(BaseModel):
    action: Literal["APPLICATION_APPLIED"] = "APPLICATION_APPLIED"
    is_applied: Literal[True] = True
    final_status_message: str = Field(description="The final status message to be shown to the user.")
    url_applied_on: str = Field(description = "URL of the page that shows job application submitted, used by other agent to verify. ")

class ApplicationRequiresLoginSubModel(BaseModel):
    is_applied: Literal[False] = False
    requires_login: Literal[True] = True
    action: Literal["REQUIRES_LOGIN"] = "REQUIRES_LOGIN"
    login_page_url: str = Field(description="URL of the page that requires login.")
    login_message: str = Field(description = "Message to be shown to the agent handling the login.")

class ApplicationSkipSubModel(BaseModel):
    action: Literal["SKIP_JOB"] = "SKIP_JOB"
    is_applied: Literal[False] = False
    reason: str = Field(description="Reason for skipping the job.")


class ApplicationRequiresHumanSubModel(BaseModel):
    is_applied: Literal[False] = False
    requires_human: Literal[True] = True
    action: Literal["REQUIRES_HUMAN"] = "REQUIRES_HUMAN"
    human_intervention_reason: Literal["CAPTCHA", "QUESTION", "SECURITY", "OTHER"] = Field(default=None, description="")
    human_intervention_prompt: str = Field(default="", description="")
    issue_page_url: str = Field(default="", description="URL of the page that requires human intervention.")
