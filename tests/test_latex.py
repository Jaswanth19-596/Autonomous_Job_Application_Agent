import unittest

from prompts import load_prompt
from utilities.latex import resume_latex_source


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
