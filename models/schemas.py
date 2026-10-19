"""Pydantic schemas for workflow decisions and extracted job data."""

from typing import Literal, Union, Annotated, Optional
from pydantic import BaseModel, Field
from models.sub_schemas import ChatWithUserSubModel, ApplyForJobsSubModel, InvalidJobSubModel, ValidJobSubModel, ApplicationAppliedSubModel, ApplicationRequiresLoginSubModel, ApplicationRequiresHumanSubModel, ApplicationUrlReadySubModel, ApplicationUrlResolutionIssueSubModel, ApplicationUrlLoginSubModel, ApplicationSkipSubModel

ApplicationNotAppliedSubModel = ApplicationRequiresHumanSubModel

UserRequestAction = Annotated[
    Union[
        ChatWithUserSubModel,
        ApplyForJobsSubModel,
    ],
    Field(discriminator="action"),
]


class UserRequestDecision(BaseModel):
    """One discriminated user-request decision suitable for structured output."""

    decision: UserRequestAction




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



ApplicationUrlDecision = Annotated[
    Union[ApplicationUrlReadySubModel, ApplicationUrlResolutionIssueSubModel, ApplicationUrlLoginSubModel],
    Field(discriminator="action"),
]

class ApplicationUrlResolutionResult(BaseModel):
    result: ApplicationUrlDecision




class AuthenticationResult(BaseModel):
    """Outcome from the dedicated login node."""

    is_logged_in: bool = Field(
        description="Whether authentication to the target employer or ATS succeeded."
    )
    logged_in_url: str = Field(
        description="The authenticated application URL, or the current URL on failure."
    )
    message: str = Field(description="This is the information that you are passing to the caller (the upstream node). It can be a request for more information or a success message or a failure message")
    requires_human: bool = Field(
        default=False,
        description="Whether human intervention is required (e.g. verifying LinkedIn/GitHub accounts, CAPTCHA, or manual 2FA)."
    )




ApplicationDecision = Annotated[
    Union[ApplicationAppliedSubModel, ApplicationRequiresLoginSubModel, ApplicationRequiresHumanSubModel, ApplicationSkipSubModel],
    Field(discriminator="action"),
]


class ApplicationResult(BaseModel):
    """One discriminated application-result decision for structured output."""

    decision: ApplicationDecision

class ApplicationVerificationAppliedSubModel(BaseModel):
    action: Literal["Applied"] = "Applied"
    evidence_source: Literal["application_site", "confirmation_email", "none"] = Field(
        default="none",
        description="Where the decisive verification evidence came from."
    )

class ApplicationVerificationNotAppliedSubModel(BaseModel):
    action: Literal["Not Applied"] = "Not Applied"
    reason_for_not_applied: str = Field(description="The reason for not applied")

    
ApplicationVerificationAction = Annotated[
    Union[ApplicationVerificationAppliedSubModel, ApplicationVerificationNotAppliedSubModel],
    Field(discriminator="action"),
]


class ApplicationVerificationDecision(BaseModel):
    """One discriminated application-verification decision for structured output."""

    decision: ApplicationVerificationAction

