from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from prompts import load_prompt
from utilities.latex import compile_resume_pdf, resume_latex_source


class _SuccessfulLatexProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""


class ResumeLatexSourceTests(unittest.TestCase):
    def test_extracts_a_document_from_a_markdown_fence(self):
        response = """Here is the tailored resume:
```latex
\\documentclass{article}
\\begin{document}
Resume
\\end{document}
```
"""

        self.assertEqual(
            resume_latex_source(response),
            "\\documentclass{article}\n\\begin{document}\nResume\n\\end{document}\n",
        )

    def test_rejects_a_conversational_model_response(self):
        with self.assertRaisesRegex(ValueError, "complete LaTeX document"):
            resume_latex_source("Please upload your reference resume first.")

    def test_rejects_a_document_without_a_document_environment(self):
        with self.assertRaisesRegex(ValueError, "missing \\\\begin\\{document\\}"):
            resume_latex_source("\\documentclass{article}\n\\end{document}")

    def test_customizer_prompt_includes_the_template_and_job_content(self):
        prompt = load_prompt(
            "resume_customizer/user.txt",
            reference_resume_tex="\\documentclass{article}\n\\begin{document}\n\\end{document}",
            company="Example Co.",
            job_title="Engineer",
            job_description="Build systems.",
            responsibilities="Ship reliable code.",
            required_skills="Python",
            preferred_skills="LaTeX",
            resume_text="Candidate resume",
            projects="Candidate projects",
        )

        self.assertIn("\\documentclass{article}", prompt)
        self.assertIn("Company: Example Co.", prompt)
        self.assertIn("Candidate resume", prompt)

    def test_upload_prompt_uses_the_browser_tool_file_paths_array(self):
        prompt = Path("prompts/apply_for_job/system.txt").read_text(encoding="utf-8")

        self.assertIn("filePaths=[<resume_file_path>]", prompt)
        self.assertNotIn("filePath=<resume_file_path>", prompt)
        self.assertIn("generic resume", prompt)
        self.assertIn("no generic resume remains", prompt)


class ResumeLatexCompileTests(unittest.IsolatedAsyncioTestCase):
    async def test_compile_returns_an_absolute_pdf_and_removes_auxiliary_files(self):
        with TemporaryDirectory() as temp_dir:
            tex_path = Path(temp_dir) / "resume.tex"
            tex_path.write_text(
                "\\documentclass{article}\n\\begin{document}\nResume\n\\end{document}\n",
                encoding="utf-8",
            )

            async def fake_create_subprocess_exec(*command, **_kwargs):
                output_dir = Path(
                    next(arg for arg in command if arg.startswith("-output-directory=")).split("=", 1)[1]
                )
                output_dir.mkdir(exist_ok=True)
                for suffix in (".pdf", ".aux", ".log", ".out"):
                    (output_dir / f"resume{suffix}").write_bytes(b"output")
                return _SuccessfulLatexProcess()

            with patch(
                "utilities.latex.asyncio.create_subprocess_exec",
                side_effect=fake_create_subprocess_exec,
            ):
                pdf_path = await compile_resume_pdf(tex_path, latex_engine="xelatex")

            self.assertTrue(pdf_path.is_absolute())
            self.assertTrue(pdf_path.is_file())
            self.assertEqual(list(pdf_path.parent.iterdir()), [pdf_path])
