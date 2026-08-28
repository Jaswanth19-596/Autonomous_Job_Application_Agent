import json
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent


@dataclass
class UserProfile:
    first_name: str
    last_name: str
    email: str
    phone_number: str
    phone_extension: str
    linkedin_url: str
    github_url: str
    portfolio_url: str

    address: str
    city: str
    state: str
    zip_code: str

    school: str
    degree: str
    field_of_study: str
    education_start_date: str
    education_end_date: str
    gpa: str

    authorized_to_work_us: bool
    visa_status: str
    us_citizen: bool
    security_clearance: bool

    desired_salary_min: int
    desired_salary_max: int
    available_start_date: str
    open_to_onsite_full_time: bool
    open_to_relocation: bool
    preferred_locations: str

    restricted_by_current_or_former_employer: bool
    application_answers: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def build_user_profile(cls, file_path: str) -> str:
        with Path(file_path).open("r", encoding="utf-8") as file:
            data = json.load(file)

        profile = cls(**data)

        def yes_no(value: bool) -> str:
            return "Yes" if value else "No"

        application_answers = "\n".join(
            f"- {item['question']}: {item['answer']}"
            for item in profile.application_answers.values()
            if item.get("question") and item.get("answer")
        ) or "- None recorded yet."

        return dedent(f"""
            CANDIDATE PROFILE — Use these details exactly. Do not invent information.

            Name: {profile.first_name} {profile.last_name}
            Email: {profile.email}
            Phone: {profile.phone_extension} {profile.phone_number}
            Address: {profile.address}, {profile.city}, {profile.state} {profile.zip_code}

            LinkedIn: {profile.linkedin_url}
            GitHub: {profile.github_url}
            Portfolio: {profile.portfolio_url}

            Education:
            - School: {profile.school}
            - Degree: {profile.degree} in {profile.field_of_study}
            - Dates: {profile.education_start_date} to {profile.education_end_date}
            - GPA: {profile.gpa}

            Work authorization:
            - Authorized to work in the United States: {yes_no(profile.authorized_to_work_us)}
            - Visa status: {profile.visa_status}
            - U.S. citizen: {yes_no(profile.us_citizen)}
            - Security clearance: {yes_no(profile.security_clearance)}

            Preferences:
            - Desired pay: ${profile.desired_salary_min:,}–${profile.desired_salary_max:,}
            - Available to start: {profile.available_start_date}
            - Open to onsite full-time: {yes_no(profile.open_to_onsite_full_time)}
            - Open to relocation: {yes_no(profile.open_to_relocation)}
            - Preferred locations: {profile.preferred_locations}

            Compliance:
            - Restricted by a current/former-employer agreement:
              {yes_no(profile.restricted_by_current_or_former_employer)}

            Confirmed application answers:
            {application_answers}
        """).strip()
