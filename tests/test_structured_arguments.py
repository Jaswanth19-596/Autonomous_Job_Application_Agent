import json
import unittest

from pydantic import ValidationError
from models.schemas import UserRequestDecision
from utilities.structured_arguments import normalize_tool_arguments


class StructuredArgumentsTests(unittest.TestCase):
    def parse(self, arguments):
        return UserRequestDecision.model_validate(normalize_tool_arguments(
            arguments, UserRequestDecision.model_json_schema()
        ))

    def test_encoded_decision(self):
        self.assertEqual(self.parse({"decision": '{"action":"CHAT"}'}).decision.action, "CHAT")

    def test_encoded_array_in_selected_union_branch(self):
        url = "https://example.com/jobs/123"
        result = self.parse({"decision": json.dumps({
            "action": "APPLY_FOR_JOBS", "job_urls": json.dumps([url]),
        })})
        self.assertEqual([str(u) for u in result.decision.job_urls], [url])

    def test_invalid_decision_still_rejected(self):
        for value in ['not json', '{"action":"UNKNOWN"}', '{"action":"APPLY_FOR_JOBS"}']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.parse({"decision": value})

    def test_text_fields_preserved(self):
        schema = {"type": "object", "properties": {"text": {"type": "string"}}}
        value = {"text": '{"action":"CHAT"}'}
        self.assertEqual(normalize_tool_arguments(value, schema), value)


if __name__ == "__main__":
    unittest.main()
