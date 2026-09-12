import json
import unittest
from unittest.mock import AsyncMock, Mock

from utilities.application_answers import ApplicationAnswer, create_application_answer_tool


class ApplicationAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def invoke_tool(self, result, **overrides):
        generator = Mock()
        generator.ainvoke = AsyncMock(return_value=result)
        model = Mock()
        model.with_structured_output.return_value = generator
        tool = create_application_answer_tool(model, {
            "job_details": {"title": "Engineer"},
            "user_profile": {"skills": ["Python"]},
            "customized_resume": "Built a Python service",
            "application_answers": {"availability": {"answer": "Immediately"}},
            "projects": "# Python service\nBuilt and operated a Python service.",
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
        context = json.loads(generator.ainvoke.call_args.args[0][1]["content"])
        self.assertEqual(context["customized_resume"], "Built a Python service")
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
            "application_answers": {}, "projects": "projects",
        })
        schema_properties = tool.args_schema.model_json_schema()["properties"]
        self.assertNotIn("job_details", schema_properties)
        self.assertNotIn("user_profile", schema_properties)
        self.assertNotIn("customized_resume", schema_properties)
        self.assertNotIn("application_answers", schema_properties)
        self.assertNotIn("projects", schema_properties)

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
        with self.assertRaises(ValueError):
            await self.invoke_tool(None)

    def test_unsupported_answer_cannot_supply_fill_text(self):
        with self.assertRaises(ValueError):
            ApplicationAnswer(status="needs_user_input", answer="Invented answer", evidence=[],
                              missing_information=["Experience missing"])


if __name__ == "__main__":
    unittest.main()
