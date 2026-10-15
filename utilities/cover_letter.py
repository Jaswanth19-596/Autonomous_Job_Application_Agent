"""Tool and helpers for generating tailored cover letters and compiling PDFs."""

import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Any, Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from prompts import load_prompt
from utilities.latex import compile_resume_pdf, resume_latex_source, safe_resume_filename_part

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def extract_cover_letter_body_text(latex_source: str) -> str:
    """Extract readable body text from a LaTeX cover letter document for form text fields."""
    start = latex_source.find(r"\begin{document}")
    end = latex_source.rfind(r"\end{document}")
    if start == -1 or end == -1:
        return latex_source

    content = latex_source[start + len(r"\begin{document}") : end]
    # Remove header center environment
    content = re.sub(r"\\begin\{center\}.*?\\end\{center\}", "", content, flags=re.DOTALL)
    # Remove comments (only % not preceded by backslash)
    content = re.sub(r"(?<!\\)%.*?$", "", content, flags=re.MULTILINE)
    # Handle LaTeX line breaks \\
    content = re.sub(r"\\\\(?:\s*\[.*?\])?", "\n", content)
    # Remove common formatting macros and commands
    content = re.sub(r"\\[a-zA-Z]+\*?(?:\[.*?\])?(?:\{.*?\})?", " ", content)
    # Clean up TeX special characters
    content = content.replace(r"\%", "%").replace(r"\&", "&").replace(r"\$", "$")
    content = content.replace(r"\_", "_").replace(r"\#", "#")
    content = content.replace(r"\quad", " ").replace(r"\cdot", "·")
    # Clean up whitespace and newlines
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in content.splitlines()]
    clean_lines = [line for line in lines if line]
    return "\n\n".join(clean_lines)


def _load_fallback_candidate_data() -> dict[str, Any]:
    """Load profile, resume, projects, and experiences from disk if not provided in context."""
    user_dir = _PROJECT_ROOT / "user"
    user_profile = {}
    profile_path = user_dir / "user_profile.json"
    if profile_path.is_file():
        try:
            user_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    resume_text = ""
    resume_path = user_dir / "resume.txt"
    if resume_path.is_file():
        resume_text = resume_path.read_text(encoding="utf-8")

    experiences = ""
    exp_dir = user_dir / "experience"
    if exp_dir.is_dir():
        experiences = "\n\n".join(
            f"# {p.relative_to(exp_dir)}\n\n{p.read_text(encoding='utf-8')}"
            for p in sorted(exp_dir.rglob("*.md")) if p.is_file()
        )

    projects = ""
    projects_dir = user_dir / "projects"
    if projects_dir.is_dir():
        projects = "\n\n".join(
            f"# {p.relative_to(projects_dir)}\n\n{p.read_text(encoding='utf-8')}"
            for p in sorted(projects_dir.rglob("*.md")) if p.is_file()
        )

    return {
        "user_profile": user_profile,
        "resume_text": resume_text,
        "experiences": experiences,
        "projects": projects,
    }


async def generate_cover_letter_pdf(
    model,
    source_context: Mapping[str, Any] | None = None,
    company_name: str = "",
    job_title: str = "",
    job_description: str = "",
    hiring_manager: str = "Hiring Team",
    additional_context: str = "",
    latex_engine: str = "xelatex",
) -> dict[str, Any]:
    """Generate LaTeX cover letter source and compile to PDF."""
    source_context = dict(source_context or {})
    fallback_data = _load_fallback_candidate_data()

    job_details = source_context.get("job_details") or {}
    if hasattr(job_details, "model_dump"):
        job_details = job_details.model_dump()

    final_company = (
        company_name.strip()
        or job_details.get("companyName")
        or "Company"
    )
    final_title = (
        job_title.strip()
        or job_details.get("title")
        or "Engineer"
    )
    final_description = (
        job_description.strip()
        or job_details.get("descriptionText")
        or ""
    )
    final_manager = hiring_manager.strip() or "Hiring Team"

    resume_text = (
        source_context.get("resume_data")
        or source_context.get("customized_resume")
        or fallback_data["resume_text"]
    )
    projects = source_context.get("projects") or fallback_data["projects"]
    if isinstance(projects, (dict, list)):
        projects = json.dumps(projects, default=str)
    experiences = source_context.get("experiences") or fallback_data["experiences"]

    ref_cover_path = Path(
        source_context.get("reference_cover_letter_path")
        or _PROJECT_ROOT / "user/master_cover_letter.tex"
    )
    if not ref_cover_path.is_file():
        ref_cover_path = _PROJECT_ROOT / "user/master_cover_letter.tex"

    reference_cover_letter_tex = (
        ref_cover_path.read_text(encoding="utf-8")
        if ref_cover_path.is_file()
        else ""
    )

    system_prompt = load_prompt("cover_letter/system.txt")
    user_prompt = load_prompt(
        "cover_letter/user.txt",
        companyName=final_company,
        title=final_title,
        descriptionText=final_description,
        hiring_manager=final_manager,
        additional_context=additional_context,
        resume_text=resume_text,
        projects=projects,
        experiences=experiences,
        reference_cover_letter_tex=reference_cover_letter_tex,
    )

    response = await model.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])

    raw_content = getattr(response, "content", "")
    if isinstance(raw_content, list):
        blocks = []
        for block in raw_content:
            if isinstance(block, dict) and block.get("text"):
                blocks.append(block["text"])
            elif hasattr(block, "text") and block.text:
                blocks.append(block.text)
        raw_text = "\n".join(blocks)
    else:
        raw_text = str(raw_content)

    latex_source = resume_latex_source(raw_text)

    safe_company = safe_resume_filename_part(final_company)
    safe_title = safe_resume_filename_part(final_title)
    resolved_output_dir = Path(
        source_context.get("output_dir")
        or Path("cover_letters") / f"{safe_company}_{safe_title}"
    )
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    tex_path = resolved_output_dir / "jaswanth_cover_letter.tex"
    tex_path.write_text(latex_source, encoding="utf-8")

    pdf_path = await compile_resume_pdf(
        tex_path,
        latex_engine=source_context.get("latex_engine", latex_engine),
    )

    body_text = extract_cover_letter_body_text(latex_source)

    return {
        "status": "success",
        "pdf_path": str(pdf_path.resolve()),
        "file_path": str(pdf_path.resolve()),
        "cover_letter_text": body_text,
        "tex_path": str(tex_path.resolve()),
        "message": f"Cover letter PDF successfully generated at {pdf_path.resolve()}",
    }


def create_cover_letter_tool(model, source_context: Mapping[str, Any] | None = None):
    """Create a cover letter generation tool with attached workflow context."""

    @tool
    async def create_cover_letter(
        company_name: str = "",
        job_title: str = "",
        job_description: str = "",
        hiring_manager: str = "Hiring Team",
        additional_context: str = "",
    ) -> str:
        """Generate a tailored cover letter and compile it to a PDF document.

        Call this tool when the job application asks for a cover letter (file upload or text).
        Optionally supply company_name, job_title, job_description, hiring_manager, or additional_context.
        The tool automatically uses the candidate's profile, resume, projects, and target job details.
        Returns a JSON string containing:
        - `pdf_path` (and `file_path`): Absolute path to the generated PDF for use with `upload_file`.
        - `cover_letter_text`: Plain text of the cover letter for form text fields.
        """
        try:
            result = await generate_cover_letter_pdf(
                model=model,
                source_context=source_context,
                company_name=company_name,
                job_title=job_title,
                job_description=job_description,
                hiring_manager=hiring_manager,
                additional_context=additional_context,
            )
            return json.dumps(result)
        except Exception as e:
            logger.exception("Failed to create cover letter: %s", e)
            return json.dumps({
                "status": "error",
                "error": str(e),
                "message": f"Cover letter creation failed: {e}",
            })

    return create_cover_letter


# Standalone tool instance with default fallback model for standalone tool invocations
@tool
async def create_cover_letter(
    company_name: str = "",
    job_title: str = "",
    job_description: str = "",
    hiring_manager: str = "Hiring Team",
    additional_context: str = "",
) -> str:
    """Generate a tailored cover letter and compile it to a PDF document.

    Call this tool when the job application asks for a cover letter (file upload or text).
    Optionally supply company_name, job_title, job_description, hiring_manager, or additional_context.
    The tool automatically uses the candidate's profile, resume, projects, and target job details.
    Returns a JSON string containing:
    - `pdf_path` (and `file_path`): Absolute path to the generated PDF for use with `upload_file`.
    - `cover_letter_text`: Plain text of the cover letter for form text fields.
    """
    from node_shared import model
    try:
        result = await generate_cover_letter_pdf(
            model=model,
            source_context=None,
            company_name=company_name,
            job_title=job_title,
            job_description=job_description,
            hiring_manager=hiring_manager,
            additional_context=additional_context,
        )
        return json.dumps(result)
    except Exception as e:
        logger.exception("Failed to create cover letter: %s", e)
        return json.dumps({
            "status": "error",
            "error": str(e),
            "message": f"Cover letter creation failed: {e}",
        })
