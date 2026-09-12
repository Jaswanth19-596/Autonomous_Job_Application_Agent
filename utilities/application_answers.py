"""A browser-free drafting tool for application questionnaire answers."""

import json
from typing import Any, Literal, Mapping

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from prompts import load_prompt
from utilities.opencode import opencode_session_headers


class ApplicationAnswer(BaseModel):
    status: Literal["answered", "needs_user_input"]
    answer: str = Field(description="Ready-to-fill text, or empty if unsupported.")
    evidence: list[str] = Field(description="Specific supplied facts supporting the answer.")
    missing_information: list[str]

    @model_validator(mode="after")
    def validate_answer(self):
        if self.status == "answered":
            if not self.answer.strip() or not self.evidence or self.missing_information:
                raise ValueError("An answered question requires text, evidence, and no missing information")
        elif self.answer or not self.missing_information:
            raise ValueError("Unsupported answers must be empty and identify missing information")
        return self


def create_application_answer_tool(model, source_context: Mapping[str, Any]):
    """Create a drafting tool with workflow-owned evidence attached.

    The browser agent supplies only the question and form-specific constraints.
    Profile, resume, job, saved answers, and project briefs come from workflow
    state so a tool call cannot silently replace them with a short summary.
    """

    required_sources = {
        "job_details", "user_profile", "customized_resume",
        "application_answers", "projects",
    }
    missing_sources = required_sources - source_context.keys()
    if missing_sources:
        raise ValueError(
            "Application-answer source context is missing: "
            + ", ".join(sorted(missing_sources))
        )

    @tool
    async def generate_application_answer(
        question: str,
        config: RunnableConfig,
        additional_context: str = "",
        answer_options: list[str] | None = None,
        max_words: int | None = None,
        max_characters: int | None = None,
    ) -> str:
        """Draft a truthful customized application answer and return JSON to the caller.

        Pass the exact question and any visible options or length limits.
        The full job details, profile, customized resume text, saved answers,
        and supporting project briefs are attached by the workflow.
        Only fill an `answered` result; `needs_user_input` identifies missing facts.
        This tool generates text only; it never fills or submits the form.
        """
        if not question.strip():
            raise ValueError("question must not be blank")
        for limit in (max_words, max_characters):
            if limit is not None and limit < 1:
                raise ValueError("Answer limits must be positive")
        context = dict(
            question=question,
            job_details=source_context["job_details"],
            user_profile=source_context["user_profile"],
            customized_resume=source_context["customized_resume"],
            application_answers=source_context["application_answers"],
            projects=source_context["projects"],
            additional_context=additional_context, answer_options=answer_options,
            max_words=max_words, max_characters=max_characters,
        )
        generator = model.with_structured_output(
            ApplicationAnswer, method="function_calling", tool_choice="auto",
            extra_headers=opencode_session_headers(config),
        )
        result = await generator.ainvoke([
            {"role": "system", "content": load_prompt("application_answer/system.txt")},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ], config=config)
        result = ApplicationAnswer.model_validate(result)
        if result.status == "answered":
            if max_words is not None and len(result.answer.split()) > max_words:
                raise ValueError("Generated answer exceeds max_words; retry with a shorter answer")
            if max_characters is not None and len(result.answer) > max_characters:
                raise ValueError("Generated answer exceeds max_characters; retry with a shorter answer")
            if answer_options and result.answer not in answer_options:
                raise ValueError("Generated answer does not match an available option")
        return result.model_dump_json()

    return generate_application_answer
