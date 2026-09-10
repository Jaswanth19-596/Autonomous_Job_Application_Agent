"""Helpers for safely saving and compiling LaTeX resume files."""

import asyncio
import re
from pathlib import Path


_SUPPORTED_LATEX_ENGINES = {"xelatex", "lualatex", "pdflatex"}
_DOCUMENT_START = r"\documentclass"
_DOCUMENT_BEGIN = r"\begin{document}"
_DOCUMENT_END = r"\end{document}"


def safe_resume_filename_part(value: str) -> str:
    """Make job metadata safe to use as part of a generated filename."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "resume"


def resume_latex_source(response_text: str) -> str:
    """Extract and validate a complete LaTeX document returned by the model.

    Models occasionally wrap code in Markdown or add a short introduction. Those
    wrappers are harmless, but a conversational response must never be saved with
    a ``.tex`` extension and passed to the compiler.
    """
    text = response_text.strip()
    start = text.find(_DOCUMENT_START)
    end = text.rfind(_DOCUMENT_END)

    if start == -1 or end == -1:
        raise ValueError(
            "Resume generation returned text instead of a complete LaTeX document "
            "(expected \\documentclass through \\end{document})."
        )

    source = text[start : end + len(_DOCUMENT_END)].strip()
    if _DOCUMENT_BEGIN not in source:
        raise ValueError(
            "Resume generation returned an incomplete LaTeX document "
            "(missing \\begin{document})."
        )

    return source + "\n"


async def compile_resume_pdf(tex_path: Path, latex_engine: str = "xelatex") -> Path:
    """Compile a resume twice so references and layout settle before publishing."""
    if latex_engine not in _SUPPORTED_LATEX_ENGINES:
        supported = ", ".join(sorted(_SUPPORTED_LATEX_ENGINES))
        raise ValueError(
            f"Unsupported LaTeX engine: {latex_engine}. Choose one of {supported}."
        )

    source = tex_path.read_text(encoding="utf-8")
    # Validate files supplied from outside the resume node, too.
    resume_latex_source(source)

    output_dir = tex_path.parent / "build"
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [
        latex_engine,
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-output-directory={output_dir.resolve()}",
        tex_path.name,
    ]

    # A second run resolves references and produces stable pagination.
    for _ in range(2):
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=tex_path.parent,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            compiler_output = (stdout + stderr).decode("utf-8", errors="replace")
            raise RuntimeError(
                "Resume PDF compilation failed. LaTeX output:\n"
                f"{compiler_output[-4000:]}"
            )

    pdf_path = output_dir / f"{tex_path.stem}.pdf"
    if not pdf_path.is_file():
        raise RuntimeError("LaTeX completed without creating the expected resume PDF.")

    return pdf_path
