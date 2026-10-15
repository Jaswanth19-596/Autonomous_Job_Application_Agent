"""Tests for cover letter generation tool and compilation."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage

from utilities.cover_letter import (
    create_cover_letter_tool,
    extract_cover_letter_body_text,
    generate_cover_letter_pdf,
)


class CoverLetterTests(unittest.IsolatedAsyncioTestCase):
    def test_extract_cover_letter_body_text(self):
        sample_latex = (
            r"\documentclass{article}" "\n"
            r"\begin{document}" "\n"
            r"\begin{center}HEADER\end{center}" "\n"
            r"\today" "\n"
            r"\textbf{Hiring Team} \\" "\n"
            r"\textbf{Example Corp}" "\n\n"
            r"Dear Hiring Team," "\n\n"
            r"I am writing to express my strong interest in the AI Engineer role." "\n"
            r"At Zenith, I improved efficiency by 50\%." "\n\n"
            r"Sincerely, \\" "\n"
            r"\textbf{Jaswanth Mada}" "\n"
            r"\end{document}"
        )
        body = extract_cover_letter_body_text(sample_latex)
        self.assertNotIn(r"\documentclass", body)
        self.assertNotIn(r"\begin{document}", body)
        self.assertNotIn("HEADER", body)
        self.assertIn("Dear Hiring Team,", body)
        self.assertIn("I am writing to express my strong interest in the AI Engineer role.", body)
        self.assertIn("50%", body)

    async def test_generate_cover_letter_pdf_flow(self):
        mock_latex = (
            r"\documentclass[10pt]{article}" "\n"
            r"\begin{document}" "\n"
            r"Dear Hiring Team, this is my customized cover letter for Example Corp." "\n"
            r"\end{document}"
        )
        mock_model = Mock(ainvoke=AsyncMock(return_value=AIMessage(content=mock_latex)))

        with tempfile.TemporaryDirectory() as tmp_dir:
            dummy_pdf = Path(tmp_dir) / "output.pdf"
            dummy_pdf.write_bytes(b"%PDF-1.4 dummy")

            with patch("utilities.cover_letter.compile_resume_pdf", AsyncMock(return_value=dummy_pdf)):
                source_context = {
                    "job_details": {
                        "companyName": "Acme Corp",
                        "title": "Staff AI Engineer",
                        "descriptionText": "Looking for multi-agent LLM systems experience.",
                    },
                    "customized_resume": "Candidate resume text",
                    "projects": "Candidate projects",
                    "experiences": "Candidate experiences",
                    "output_dir": tmp_dir,
                }
                result = await generate_cover_letter_pdf(
                    model=mock_model,
                    source_context=source_context,
                    company_name="Acme Corp",
                    job_title="Staff AI Engineer",
                )

                self.assertEqual(result["status"], "success")
                self.assertEqual(result["pdf_path"], str(dummy_pdf.resolve()))
                self.assertEqual(result["file_path"], str(dummy_pdf.resolve()))
                self.assertIn("Dear Hiring Team", result["cover_letter_text"])
                mock_model.ainvoke.assert_awaited_once()

    async def test_create_cover_letter_tool_execution(self):
        mock_latex = (
            r"\documentclass[10pt]{article}" "\n"
            r"\begin{document}" "\n"
            r"Dear Hiring Team," "\n\n"
            r"Cover letter content." "\n"
            r"\end{document}"
        )
        mock_model = Mock(ainvoke=AsyncMock(return_value=AIMessage(content=mock_latex)))

        with tempfile.TemporaryDirectory() as tmp_dir:
            dummy_pdf = Path(tmp_dir) / "jaswanth_cover_letter.pdf"
            dummy_pdf.write_bytes(b"%PDF-1.4")

            with patch("utilities.cover_letter.compile_resume_pdf", AsyncMock(return_value=dummy_pdf)):
                tool_fn = create_cover_letter_tool(
                    mock_model,
                    source_context={
                        "job_details": {"companyName": "TestCo", "title": "Developer"},
                        "customized_resume": "Resume",
                        "projects": "Projects",
                        "experiences": "Experiences",
                        "output_dir": tmp_dir,
                    },
                )
                output_str = await tool_fn.ainvoke({
                    "company_name": "TestCo",
                    "job_title": "Developer",
                })
                data = json.loads(output_str)
                self.assertEqual(data["status"], "success")
                self.assertEqual(data["pdf_path"], str(dummy_pdf.resolve()))
                self.assertIn("Cover letter content", data["cover_letter_text"])

    async def test_create_cover_letter_tool_fallback_args(self):
        mock_latex = (
            r"\documentclass[10pt]{article}" "\n"
            r"\begin{document}" "\n"
            r"Dear Hiring Team," "\n\n"
            r"Cover letter content." "\n"
            r"\end{document}"
        )
        mock_model = Mock(ainvoke=AsyncMock(return_value=AIMessage(content=mock_latex)))

        with tempfile.TemporaryDirectory() as tmp_dir:
            dummy_pdf = Path(tmp_dir) / "jaswanth_cover_letter.pdf"
            dummy_pdf.write_bytes(b"%PDF-1.4")

            with patch("utilities.cover_letter.compile_resume_pdf", AsyncMock(return_value=dummy_pdf)):
                tool_fn = create_cover_letter_tool(
                    mock_model,
                    source_context={
                        "job_details": {"companyName": "FallbackCorp", "title": "Lead AI Engineer"},
                        "customized_resume": "Resume",
                        "projects": "Projects",
                        "experiences": "Experiences",
                        "output_dir": tmp_dir,
                    },
                )
                output_str = await tool_fn.ainvoke({})
                data = json.loads(output_str)
                self.assertEqual(data["status"], "success")
                self.assertEqual(data["pdf_path"], str(dummy_pdf.resolve()))

    async def test_tool_node_dispatches_create_cover_letter(self):
        from node_shared import tool_node
        mock_latex = (
            r"\documentclass[10pt]{article}" "\n"
            r"\begin{document}" "\n"
            r"Dear Hiring Team," "\n\n"
            r"Cover letter content." "\n"
            r"\end{document}"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            dummy_pdf = Path(tmp_dir) / "jaswanth_cover_letter.pdf"
            dummy_pdf.write_bytes(b"%PDF-1.4")

            with patch("utilities.cover_letter.compile_resume_pdf", AsyncMock(return_value=dummy_pdf)), \
                 patch("node_shared.application_answer_model", Mock(ainvoke=AsyncMock(return_value=AIMessage(content=mock_latex)))):
                state = {
                    "tool_call_origin": "submit_job_application",
                    "submit_job_application_messages": [
                        AIMessage(
                            content="",
                            tool_calls=[{
                                "id": "call_123",
                                "name": "create_cover_letter",
                                "args": {
                                    "company_name": "OpenAI",
                                    "job_title": "Research Engineer",
                                },
                            }],
                        )
                    ],
                    "job_details": {"companyName": "OpenAI", "title": "Research Engineer"},
                    "customized_resume": "Customized resume",
                    "user_profile": {"name": "Jaswanth"},
                    "output_dir": tmp_dir,
                }
                result = await tool_node(state)
                messages = result["submit_job_application_messages"]
                self.assertEqual(len(messages), 1)
                tool_msg = messages[0]
                self.assertEqual(tool_msg.name, "create_cover_letter")
                parsed = json.loads(tool_msg.content)
                self.assertEqual(parsed["status"], "success")
                self.assertEqual(parsed["pdf_path"], str(dummy_pdf.resolve()))


if __name__ == "__main__":
    unittest.main()
