from pydantic import BaseModel, Field
from typing import Optional


class FetchedJobDetails(BaseModel):
    trackingId: Optional[str] = Field(
        default=None,
        description="Tracking ID of the job if explicitly available on the page."
    )

    refId: Optional[str] = Field(
        default=None,
        description="Reference or requisition ID of the job if explicitly available."
    )

    link: Optional[str] = Field(
        default=None,
        description="Canonical URL of the job listing."
    )

    title: Optional[str] = Field(
        default=None,
        description="Job title."
    )

    companyName: Optional[str] = Field(
        default=None,
        description="Name of the company offering the job."
    )

    companyLinkedinUrl: Optional[str] = Field(
        default=None,
        description="LinkedIn URL of the company if explicitly available."
    )

    companyLogo: Optional[str] = Field(
        default=None,
        description="URL of the company logo if available."
    )

    location: Optional[str] = Field(
        default=None,
        description="Job location, such as city, state, country, remote, or hybrid."
    )

    postedAt: Optional[str] = Field(
        default=None,
        description="Date or relative time when the job was posted."
    )

    benefits: list[str] = Field(
        default_factory=list,
        description="Benefits explicitly mentioned in the job posting."
    )

    descriptionHtml: Optional[str] = Field(
        default=None,
        description="Raw HTML of the job description if provided in the input."
    )

    applicantsCount: Optional[int] = Field(
        default=None,
        description="Number of applicants if explicitly shown."
    )

    applyUrl: Optional[str] = Field(
        default=None,
        description="URL used to apply for the job."
    )

    salary: Optional[str] = Field(
        default=None,
        description="Salary or compensation range exactly as stated in the posting."
    )

    descriptionText: Optional[str] = Field(
        default=None,
        description="Clean plain-text job description."
    )

    seniorityLevel: Optional[str] = Field(
        default=None,
        description="Seniority level, such as Entry level, Associate, Mid-Senior, Director, or Executive."
    )

    employmentType: Optional[str] = Field(
        default=None,
        description="Employment type such as Full-time, Part-time, Contract, Internship, or Temporary."
    )

    jobFunction: list[str] = Field(
        default_factory=list,
        description="Job functions explicitly stated or clearly identifiable from the posting."
    )

    industries: list[str] = Field(
        default_factory=list,
        description="Industries associated with the position or company."
    )

    inputUrl: Optional[str] = Field(
        default=None,
        description="Original job URL supplied as input."
    )

    companyAddress: Optional[str] = Field(
        default=None,
        description="Company address if explicitly available."
    )

    companyWebsite: Optional[str] = Field(
        default=None,
        description="Official company website if explicitly available."
    )

    companySlogan: Optional[str] = Field(
        default=None,
        description="Company slogan or tagline if explicitly available."
    )

    companyDescription: Optional[str] = Field(
        default=None,
        description="Short description of the company based only on information provided on the page."
    )

    companyEmployeesCount: Optional[str] = Field(
        default=None,
        description="Company employee count or employee range if available."
    )

    jobPosterName: Optional[str] = Field(
        default=None,
        description="Name of the person who posted the job if available."
    )

    jobPosterTitle: Optional[str] = Field(
        default=None,
        description="Job title of the person who posted the job."
    )

    jobPosterPhoto: Optional[str] = Field(
        default=None,
        description="URL of the job poster's profile photo if available."
    )

    jobPosterProfileUrl: Optional[str] = Field(
        default=None,
        description="Profile URL of the job poster if available."
    )

    jobboard: Optional[str] = Field(
        default=None,
        description="Job board or ATS hosting the listing, such as LinkedIn, Workday, Greenhouse, Lever, or Indeed."
    )

    application_url: Optional[str] = Field(
        default=None,
        description="Final external application URL if the job redirects to another application website."
    )