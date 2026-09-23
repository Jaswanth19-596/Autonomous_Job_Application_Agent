import ast
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock

from utilities.application_answers import ApplicationAnswer, create_application_answer_tool


class ApplicationAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def invoke_tool(self, result, *, responses=None, **overrides):
        generator = Mock()
        generator.ainvoke = AsyncMock(return_value=result)
        if responses is not None:
            generator.ainvoke.side_effect = responses
        model = Mock()
        model.with_structured_output.return_value = generator
        tool = create_application_answer_tool(model, {
            "job_details": {"title": "Engineer"},
            "user_profile": {"skills": ["Python"]},
            "customized_resume": "Built a Python service",
            "application_answers": {"availability": {"answer": "Immediately"}},
            "projects": "# Python service\nBuilt and operated a Python service.",
            "experiences": "# Employer\nOperated retrieval services.",
        })
        args = dict(question="Describe your Python experience")
        args.update(overrides)
        response = await tool.ainvoke(args, config={"configurable": {"thread_id": "test-answer"}})
        return json.loads(response), generator, model

    async def test_context_and_answer_round_trip(self):
        expected = dict(status="answered", answer="I built a Python service.",
                        evidence=["customized_resume: Built a Python service"], missing_information=[])
        actual, generator, model = await self.invoke_tool(expected)
        self.assertEqual(actual, expected)
        self.assertEqual(generator.ainvoke.await_count, 1)
        self.assertEqual(model.with_structured_output.call_args.kwargs["tool_choice"],
                         "ApplicationAnswer")
        context = json.loads(generator.ainvoke.call_args.args[0][1]["content"])
        self.assertEqual(context["customized_resume"], "Built a Python service")
        self.assertEqual(context["experiences"], "# Employer\nOperated retrieval services.")
        self.assertNotIn("resume_data", context)
        self.assertEqual(context["job_details"], {"title": "Engineer"})
        self.assertEqual(context["projects"], "# Python service\nBuilt and operated a Python service.")
        self.assertEqual(context["application_answers"], {"availability": {"answer": "Immediately"}})
        self.assertEqual(model.with_structured_output.call_args.kwargs["extra_headers"],
                         {"x-opencode-session": "test-answer"})

    async def test_workflow_sources_are_not_tool_arguments(self):
        _, generator, _ = await self.invoke_tool(dict(
            status="needs_user_input", answer="", evidence=[],
            missing_information=["A supported fact is required."],
        ))
        properties = generator.ainvoke.call_args.args[0][1]
        self.assertIn("projects", properties["content"])

        tool = create_application_answer_tool(Mock(), {
            "job_details": {}, "user_profile": {}, "customized_resume": "resume",
            "application_answers": {}, "projects": "projects", "experiences": "experience",
        })
        schema_properties = tool.args_schema.model_json_schema()["properties"]
        self.assertNotIn("job_details", schema_properties)
        self.assertNotIn("user_profile", schema_properties)
        self.assertNotIn("customized_resume", schema_properties)
        self.assertNotIn("application_answers", schema_properties)
        self.assertNotIn("projects", schema_properties)
        self.assertNotIn("experiences", schema_properties)

    async def test_missing_information(self):
        result, _, _ = await self.invoke_tool(dict(status="needs_user_input", answer="",
            evidence=[], missing_information=["How many years have you used Python?"]))
        self.assertEqual(result["answer"], "")

    async def test_rejects_unusable_answers(self):
        result = dict(status="answered", answer="I built a Python service.",
                      evidence=["resume"], missing_information=[])
        for limits in ({"max_words": 2}, {"max_characters": 3}, {"answer_options": ["Yes", "No"]}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                await self.invoke_tool(result, **limits)

    async def test_missing_structured_output(self):
        result, generator, _ = await self.invoke_tool(None)
        self.assertIn("no ApplicationAnswer after two attempts", result["error"])
        self.assertNotIn("answer", result)
        self.assertNotIn("missing_information", result)
        self.assertEqual(generator.ainvoke.await_count, 2)

    async def test_retries_missing_structured_output(self):
        expected = dict(status="answered", answer="I built a Python service.",
                        evidence=["customized_resume: Built a Python service"], missing_information=[])
        result, generator, _ = await self.invoke_tool(None, responses=[None, expected])
        self.assertEqual(result, expected)
        self.assertEqual(generator.ainvoke.await_count, 2)
        first, second = generator.ainvoke.await_args_list
        self.assertEqual(first.args[0], second.args[0][:2])
        self.assertIn("ApplicationAnswer tool", second.args[0][-1]["content"])
        self.assertEqual(first.kwargs["config"], second.kwargs["config"])

    def test_experience_briefs_reach_workflow_answer_sources(self):
        root = Path(__file__).resolve().parents[1]
        sources = [root / "user_nodes.py", root / "node_shared.py"]
        functions = [node for source in sources for node in ast.parse(source.read_text()).body
                     if isinstance(node, ast.FunctionDef)
                     and node.name in {"initialize_user_data", "application_answer_source"}]
        for node in functions:
            node.decorator_list = []
        namespace = dict(
            __file__=str(root / "nodes.py"), Path=Path, json=json,
            log_node_stage=Mock(), load_prompt=Mock(return_value="prompt"),
            applicable_profile=lambda profile, state: profile,
        )
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(root / "nodes.py"), "exec"), namespace)
        state = namespace["initialize_user_data"]({})
        for brief in (root / "user/experience").glob("*.md"):
            self.assertIn(brief.name, state["experiences"])
            self.assertIn(brief.read_text(), state["experiences"])
        state["customized_resume"] = "Resume"
        context = namespace["application_answer_source"](state, {"skills": ["Python"]})
        self.assertEqual(context["experiences"], state["experiences"])

    def test_unsupported_answer_cannot_supply_fill_text(self):
        with self.assertRaises(ValueError):
            ApplicationAnswer(status="needs_user_input", answer="Invented answer", evidence=[],
                              missing_information=["Experience missing"])


if __name__ == "__main__":
    unittest.main()
