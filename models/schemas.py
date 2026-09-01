"""Pydantic schemas for workflow decisions and extracted job data."""

from pydantic import HttpUrl
from typing import Literal, Optional

from pydantic import BaseModel, Field


class JobQueryFilter(BaseModel):
    query: str = Field(description="The query to be used for searching jobs.")
    n: int = Field(ge = 1, le = 5, default = 5, description = "Number of jobs to fetch")
    sort_by: Literal["postedAt"] = "postedAt"
    order: Literal["desc", "asc"] = "desc"


class FetchJobDescriptionDecision(BaseModel):
    job_url: str = Field(
        description=(
            "The exact job-posting URL supplied by the user or selected"),
    )

class DispatchJobsDecision(BaseModel):
    job_query_filter : JobQueryFilter = Field(description="Job search filter to be used for searching jobs")


class FetchJobrightJobsDecision(BaseModel):
    """The number of ranked Jobright recommendations to import."""

    job_count: int = Field(
        ge=1,
        le=5,
        default=5,
        description="Number of Jobright Top Matched recommendations to fetch (1 through 5).",
    )


class UserRequestRoutingDecision(BaseModel):
    next_step: Literal[
        "CHAT", "FETCH_JOB_DESCRIPTION", "DISPATCH_JOBS", "FETCH_JOBRIGHT_JOBS", "END"
    ] = "CHAT"
    
    fetch_job_description_decision : Optional[FetchJobDescriptionDecision] = Field(default=None,description="Required if next_step is FETCH_JOB_DESCRIPTION.")
    
    dispatch_jobs_decision : Optional[DispatchJobsDecision] = Field(default=None, description="Required if next_step is DISPATCH_JOBS")

    fetch_jobright_jobs_decision: Optional[FetchJobrightJobsDecision] = Field(
        default=None,
        description="Required if next_step is FETCH_JOBRIGHT_JOBS.",
    )


class JobrightJob(BaseModel):
    """One visible job card from Jobright's Top Matched recommendations."""

    rank: int = Field(description="Its displayed position from 1 to n.")
    link: str = Field(description="The Jobright detail URL used to inspect this recommendation.")
    title: str = Field(description="Job title exactly as displayed.")
    companyName: str = Field(description="Hiring company exactly as displayed.")
    location: str = Field(default="", description="Displayed location and remote/hybrid mode.")
    postedAt: str = Field(default="", description="Displayed posting age or date.")
    applyUrl: str = Field(
        description=(
            "Final external employer or applicant-tracking-system URL reached from "
            "Jobright's Apply action. It must not be a Jobright URL."
        )
    )
    salary: str = Field(default="", description="Displayed salary range, if any.")
    descriptionText: str = Field(default="", description="Short visible job/company summary only.")
    seniorityLevel: str = Field(default="", description="Displayed experience or seniority level.")
    employmentType: str = Field(default="", description="Displayed employment type.")
    matchScore: str = Field(default="", description="Displayed Jobright match score; not written to Excel.")


class JobrightJobsResult(BaseModel):
    """The n recommendations shown in Jobright's existing Top Matched order."""

    jobs: list[JobrightJob] = Field(
        min_length=1,
        max_length=5,
        description="Exactly the first n visible Top Matched jobs, in displayed order.",
    )


class JobListingDetails(BaseModel):
    trackingId: Optional[str] = Field(default=None, description="Tracking ID of the job if explicitly available on the page.")
    refId: Optional[str] = Field(default=None, description="Reference or requisition ID of the job if explicitly available.")
    link: Optional[str] = Field(default=None, description="Canonical URL of the job listing.")
    title: Optional[str] = Field(default=None, description="Job title.")
    companyName: Optional[str] = Field(default=None, description="Name of the company offering the job.")
    companyLinkedinUrl: Optional[str] = Field(default=None, description="LinkedIn URL of the company if explicitly available.")
    companyLogo: Optional[str] = Field(default=None, description="URL of the company logo if available.")
    location: Optional[str] = Field(default=None, description="Job location, such as city, state, country, remote, or hybrid.")
    postedAt: Optional[str] = Field(default=None, description="Date or relative time when the job was posted.")
    benefits: list[str] = Field(default_factory=list, description="Benefits explicitly mentioned in the job posting.")
    descriptionHtml: Optional[str] = Field(default=None, description="Raw HTML of the job description if provided in the input.")
    applicantsCount: Optional[int] = Field(default=None, description="Number of applicants if explicitly shown.")
    applyUrl: Optional[str] = Field(default=None, description="URL used to apply for the job.")
    salary: Optional[str] = Field(default=None, description="Salary or compensation range exactly as stated in the posting.")
    descriptionText: Optional[str] = Field(default=None, description="Clean plain-text job description.")
    seniorityLevel: Optional[str] = Field(default=None, description="Seniority level, such as Entry level, Associate, Mid-Senior, Director, or Executive.")
    employmentType: Optional[str] = Field(default=None, description="Employment type such as Full-time, Part-time, Contract, Internship, or Temporary.")
    jobFunction: list[str] = Field(default_factory=list, description="Job functions explicitly stated or clearly identifiable from the posting.")
    industries: list[str] = Field(default_factory=list, description="Industries associated with the position or company.")
    inputUrl: Optional[str] = Field(default=None, description="Original job URL supplied as input.")
    companyAddress: Optional[str] = Field(default=None, description="Company address if explicitly available.")
    companyWebsite: Optional[str] = Field(default=None, description="Official company website if explicitly available.")
    companySlogan: Optional[str] = Field(default=None, description="Company slogan or tagline if explicitly available.")
    companyDescription: Optional[str] = Field(default=None, description="Short description of the company based only on information provided on the page.")
    companyEmployeesCount: Optional[str] = Field(default=None, description="Company employee count or employee range if available.")
    jobPosterName: Optional[str] = Field(default=None, description="Name of the person who posted the job if available.")
    jobPosterTitle: Optional[str] = Field(default=None, description="Job title of the person who posted the job.")
    jobPosterPhoto: Optional[str] = Field(default=None, description="URL of the job poster's profile photo if available.")
    jobPosterProfileUrl: Optional[str] = Field(default=None, description="Profile URL of the job poster if available.")
    jobboard: Optional[str] = Field(default=None, description="Job board or ATS hosting the listing, such as LinkedIn, Workday, Greenhouse, Lever, or Indeed.")
    application_url: Optional[str] = Field(default=None, description="Final external application URL if the job redirects to another application website.")


class JobValidationResult(BaseModel):
    is_valid: bool = Field(description="True if the job is valid, False otherwise")
    reason_for_not_applying: str = Field(description="Reason for not applying if the job is not valid. Leave it blank if the job is valid.")

class ApplicationAccessStatus(BaseModel):
    requires_login: bool = Field(description="True if the application requires login, False otherwise")
    actual_job_url: str = Field(description="The URL to navigate to after login")
    message: str = Field(
        description="A concise explanation of the application access status."
    )


class AuthenticationResult(BaseModel):
    is_logged_in: bool = Field(
        description="True if the login is successful, False otherwise"
    )
    logged_in_url: str = Field(
        description="The URL to navigate to after login"
    )
    message: str = Field(
        default="", description="A concise explanation of the authentication result."
    )

class ApplicationResult(BaseModel):
    is_applied: bool = Field(description="True if the application is successful, False otherwise")
    final_status_message: str = Field(description="The final status message to be shown to the user.")
    is_duplicate: bool = Field(description="True if the job is already applied for, False otherwise")
    url_applied_on: str = Field(description = "URL of the page that shows job application submitted, used by other agent to verify. ")
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
    human_intervention_prompt: str = Field(
        default="",
        description="A concise, actionable instruction for the human to complete the blocker on the current page; empty when requires_human is False.",
    )


class ApplicationVerificationResult(BaseModel):
    application_status: Literal[
        "Applied", "Not Applied", "Failed", "Needs CAPTCHA", "In Progress"
    ] = Field(description="The verified application status shown by the application site.")
    message: str = Field(
        description="A concise explanation of the evidence supporting the verified status."
    )
