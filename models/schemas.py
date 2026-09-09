"""Pydantic schemas for workflow decisions and extracted job data."""

from typing import Literal, Union, Annotated, Optional
from pydantic import BaseModel, Field
from models.sub_schemas import ChatWithUserSubModel, CustomURLFromUserSubModel, DispatchJobsSubModel, FetchJobrightJobsSubModel, JobQueryFilter, InvalidJobSubModel, ValidJobSubModel


UserRequestAction = Annotated[
    Union[
        ChatWithUserSubModel,
        CustomURLFromUserSubModel,
        DispatchJobsSubModel,
        FetchJobrightJobsSubModel,
    ],
    Field(discriminator="action"),
]


class UserRequestDecision(BaseModel):
    """One discriminated user-request decision suitable for structured output."""

    decision: UserRequestAction


class JobrightJob(BaseModel):
    """One visible job card from Jobright's Top Matched recommendations."""
    link: str = Field(description="The Jobright detail URL used to inspect this recommendation.")


class JobDetailsModel(BaseModel):
    title: str | None = Field(default=None, description="Job title.")
    companyName: str | None = Field(default=None, description="Name of the company offering the job.")
    location: str | None = Field(default=None, description="Job location, such as city, state, country, remote, or hybrid.")
    postedAt: str | None = Field(default=None, description="Date or relative time when the job was posted.")
    applyUrl: str | None = Field(default=None, description="URL used to apply for the job.")
    salary: str | None = Field(default=None, description="Salary or compensation range exactly as stated in the posting.")
    descriptionText: str | None = Field(default=None, description="Clean plain-text job description.")
    seniorityLevel: str | None = Field(default=None, description="Seniority level, such as Entry level, Associate, Mid-Senior, Director, or Executive.")
    employmentType: str | None = Field(default=None, description="Employment type such as Full-time, Part-time, Contract, Internship, or Temporary.")
    jobboard: str | None = Field(default=None, description="Job board or ATS hosting the listing, such as LinkedIn, Workday, Greenhouse, Lever, or Indeed.")



ValidJobDecision = Annotated[
    Union[ValidJobSubModel, InvalidJobSubModel],
    Field(discriminator="valid_job"),
]


class ValidJobOrNotDecision(BaseModel):
    """One discriminated job-eligibility decision for structured output."""

    decision: ValidJobDecision


class ApplicationUrlResolution(BaseModel):
    """The application URL and whether it must be authenticated first."""

    ready_to_apply: bool = Field(
        description=(
            "True when the application form is accessible without any further "
            "authentication. False when access is blocked or unresolved."
        )
    )
    requires_login: bool = Field(
        default=False,
        description=(
            "True when sign-in, account creation, or required SSO blocks the "
            "application form and the login node should handle it."
        ),
    )
    actual_job_url: str = Field(
        description=(
            "The final accessible application URL or the current login, blocked, "
            "or unresolved URL when ready_to_apply is false."
        )
    )
    message: str = Field(
        description="A concise summary of URL resolution and any authentication outcome."
    )


class AuthenticationResult(BaseModel):
    """Outcome from the dedicated login node."""

    is_logged_in: bool = Field(
        description="Whether authentication to the target employer or ATS succeeded."
    )
    logged_in_url: str = Field(
        description="The authenticated application URL, or the current URL on failure."
    )
    message: str = Field(description="A concise summary of the login result.")




class ApplicationAppliedSubModel(BaseModel):
    is_applied: Literal[True] = True
    final_status_message: str = Field(description="The final status message to be shown to the user.")
    url_applied_on: str = Field(description = "URL of the page that shows job application submitted, used by other agent to verify. ")


class ApplicationNotAppliedSubModel(BaseModel):
    is_applied: Literal[False] = False
    requires_login: bool = Field(
        default=False,
        description=(
            "True only when a normal sign-in, account-creation, or SSO gate is "
            "blocking the application and the login node should handle it."
        ),
    )
    requires_human: bool = Field(
        description=(
            "True only for a CAPTCHA, MFA/security challenge, or another "
            "required action the user must complete; otherwise False."
        )
    )
    human_intervention_reason: Optional[Literal["CAPTCHA", "OTHER"]] = Field(
        default=None,
        description="CAPTCHA for a CAPTCHA; OTHER for any other human-only blocker; null when requires_human is False.",
    )
    human_intervention_prompt: Optional[str] = Field(
        default="",
        description="A concise, actionable instruction for the human to complete the blocker on the current page; empty when requires_human is False.",
    )


ApplicationDecision = Annotated[
    Union[ApplicationAppliedSubModel, ApplicationNotAppliedSubModel],
    Field(discriminator="is_applied"),
]


class ApplicationResult(BaseModel):
    """One discriminated application-result decision for structured output."""

    decision: ApplicationDecision

class ApplicationVerificationResult(BaseModel):
    application_status: Literal[
        "Applied", "Not Applied", "Failed", "Needs CAPTCHA", "In Progress"
    ] = Field(
        description=(
            "The verified application status, supported either by the application "
            "site or by a matching application-confirmation email."
        )
    )
    evidence_source: Literal["application_site", "confirmation_email", "none"] = Field(
        default="none",
        description="Where the decisive verification evidence came from."
    )
    message: str = Field(
        description="A concise explanation of the evidence supporting the verified status."
    )
