from pydantic import BaseModel, Field
from typing import Literal

class JobQueryFilter(BaseModel):
    query: str = Field(description="The query to be used for searching jobs.")
    n: int = Field(ge = 1, le = 5, default = 5, description = "Number of jobs to fetch")
    sort_by: Literal["postedAt"] = "postedAt"
    order: Literal["desc", "asc"] = "desc"


class ChatWithUserSubModel(BaseModel):
    action: Literal["CHAT"]

# class CustomURLFromUserSubModel(BaseModel):
    # action: Literal["NEW_JOB_URL"]
    # job_url: str = Field(
    #     description=(
    #         "The exact job-posting URL supplied by the user or selected"),
    # )

class ApplyForJobsSubModel(BaseModel):
    action: Literal["APPLY_FOR_JOBS"]
    job_urls: list[str] = Field(description = "List of job URLs to apply for")


class FetchJobrightJobsSubModel(BaseModel):
    action: Literal["FETCH_JOBRIGHT_JOBS"]
    job_count: int = Field(
        ge=1,
        le=5,
        default=5,
        description="Number of Jobright Top Matched recommendations to fetch (1 through 5).",
    )


class ValidJobSubModel(BaseModel):
    valid_job: Literal["YES"]


class InvalidJobSubModel(BaseModel):
    valid_job: Literal["NO"]
    invalid_job_reason: str = Field(description="Reason for not applying if the job is not valid. Leave it blank if the job is valid.")
