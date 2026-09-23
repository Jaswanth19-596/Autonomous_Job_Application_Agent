"""Check the extraction-to-customization prompt contract without live inference."""

import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from models.schemas import JobDetailsModel
from prompts import load_prompt


class StopBeforeGeneration(Exception):
    """Stop after capturing the model input, before writing resume artifacts."""


class ResumeCustomizationTests(unittest.IsolatedAsyncioTestCase):
    async def capture_prompt(self, job_details, *, experience_state=None):
        # Use the real node and prompt loader; isolate model startup and output.
        source = Path(__file__).resolve().parents[1] / "job_nodes.py"
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "customize_resume")
        node.decorator_list = []
        model = Mock(ainvoke=AsyncMock(side_effect=StopBeforeGeneration))
        namespace = dict(
            __file__=str(source), Path=Path, log_node_stage=Mock(), load_prompt=load_prompt,
            get_custom_model_with_tools=AsyncMock(return_value=model),
        )
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "reference.tex"
            template.write_text("Reference template")
            with self.assertRaises(StopBeforeGeneration):
                await namespace["customize_resume"]({
                    "job_details": job_details,
                    "resume_data": "Candidate experience",
                    "projects": "Candidate projects",
                    **({"experiences": "Candidate employment details"}
                       if experience_state is None else experience_state),
                    "reference_resume_path": str(template),
                })
        model.ainvoke.assert_awaited_once()
        return model.ainvoke.call_args.args[0][1]["content"]

    async def test_extracted_job_reaches_actual_model_prompt(self):
        description = (
            "Build retrieval evaluation pipelines.\n"
            "Required: Python and SQL. Preferred: search relevance experience.\n"
            "Own {quality} metrics."
        )
        details = JobDetailsModel(
            title="AI Engineer", companyName="Example Labs", descriptionText=description,
        ).model_dump()
        prompt = await self.capture_prompt(details)
        self.assertIn("Company: Example Labs", prompt)
        self.assertIn("Title: AI Engineer", prompt)
        self.assertIn(description, prompt)
        self.assertIn("Candidate experience", prompt)
        self.assertIn("Candidate projects", prompt)
        self.assertIn("Candidate employment details", prompt)
        self.assertIn("Reference template", prompt)
        self.assertNotIn("Responsibilities: \n", prompt)
        self.assertNotIn("Required skills: \n", prompt)

    async def test_optional_null_fields_do_not_become_literal_none(self):
        prompt = await self.capture_prompt(JobDetailsModel().model_dump())
        self.assertNotIn("None", prompt)
        self.assertIn("Company: \nTitle: \n", prompt)

    async def test_older_checkpoints_load_experience_briefs(self):
        briefs_dir = Path(__file__).resolve().parents[1] / "user/experience"
        briefs = sorted(briefs_dir.glob("*.md"))
        self.assertTrue(briefs)
        for state in ({}, {"experiences": None}, {"experiences": ""}):
            with self.subTest(state=state):
                prompt = await self.capture_prompt(
                    JobDetailsModel().model_dump(), experience_state=state,
                )
                for brief in briefs:
                    self.assertIn(brief.name, prompt)
                    self.assertIn(brief.read_text(), prompt)
                self.assertNotIn("{experiences}", prompt)
