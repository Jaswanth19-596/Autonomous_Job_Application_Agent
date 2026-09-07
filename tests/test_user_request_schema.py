import unittest
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from models.schemas import UserRequestDecision, ValidJobOrNotDecision
from models.sub_schemas import (
    DispatchJobsSubModel,
    JobQueryFilter,
    ValidJobSubModel,
)
import nodes


class UserRequestDecisionTests(unittest.IsolatedAsyncioTestCase):
    def test_wraps_the_discriminated_job_validation_in_a_pydantic_model(self):
        decision = ValidJobOrNotDecision.model_validate(
            {"decision": {"valid_job": "YES"}}
        )

        self.assertIsInstance(decision.decision, ValidJobSubModel)

    def test_legacy_checkpoint_filter_is_explicitly_allowed(self):
        serializer = JsonPlusSerializer(
            allowed_msgpack_modules=[("models.sub_schemas", "JobQueryFilter")]
        )
        job_filter = JobQueryFilter(query="data engineer", n=2)

        restored_filter = serializer.loads_typed(serializer.dumps_typed(job_filter))

        self.assertEqual(restored_filter, job_filter)

    def test_wraps_the_discriminated_decision_in_a_pydantic_model(self):
        decision = UserRequestDecision.model_validate(
            {
                "decision": {
                    "action": "APPLY_FOR_JOBS",
                    "job_query_filter": {
                        "query": "data engineer",
                        "n": 2,
                        "sort_by": "postedAt",
                        "order": "desc",
                    },
                }
            }
        )

        self.assertIsInstance(decision.decision, DispatchJobsSubModel)
        self.assertEqual(decision.decision.job_query_filter.query, "data engineer")

    async def test_persists_job_query_filter_as_a_plain_dictionary(self):
        browser_model = AsyncMock()
        browser_model.ainvoke.return_value = type(
            "Response", (), {"tool_calls": [], "content": "I will apply to two jobs."}
        )()
        structured_model = AsyncMock()
        structured_model.ainvoke.return_value = UserRequestDecision(
            decision=DispatchJobsSubModel(
                action="APPLY_FOR_JOBS",
                job_query_filter=JobQueryFilter(query="data engineer", n=2),
            )
        )

        async def pass_through_progress(_node, _activity, awaitable, **_kwargs):
            return await awaitable

        with (
            patch.object(
                nodes,
                "get_custom_model_with_tools",
                new_callable=AsyncMock,
                return_value=browser_model,
            ),
            patch.object(nodes, "get_custom_structured_model", return_value=structured_model),
            patch.object(nodes, "await_with_progress", side_effect=pass_through_progress),
        ):
            result = await nodes.user_request_llm_node(
                {
                    "messages": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "apply to two data engineer jobs"},
                    ]
                }
            )

        self.assertEqual(result["user_node_decision"], "APPLY_FOR_JOBS")
        self.assertEqual(
            result["job_query_filter"],
            {"query": "data engineer", "n": 2, "sort_by": "postedAt", "order": "desc"},
        )

    async def test_eligible_job_validation_uses_the_wrapped_decision(self):
        browser_model = AsyncMock()
        browser_model.ainvoke.return_value = type("Response", (), {"tool_calls": []})()
        structured_model = AsyncMock()
        structured_model.ainvoke.return_value = ValidJobOrNotDecision(
            decision=ValidJobSubModel(valid_job="YES")
        )

        async def pass_through_progress(_node, _activity, awaitable, **_kwargs):
            return await awaitable

        with (
            patch.object(
                nodes,
                "get_custom_model_with_tools",
                new_callable=AsyncMock,
                return_value=browser_model,
            ),
            patch.object(nodes, "get_custom_structured_model", return_value=structured_model),
            patch.object(nodes, "await_with_progress", side_effect=pass_through_progress),
        ):
            result = await nodes.is_job_valid(
                {
                    "is_job_valid_messages": [
                        {"role": "system", "content": "instructions"},
                        {"role": "user", "content": "validate this job"},
                    ]
                }
            )

        self.assertEqual(result["validation_phase"], "ELIGIBLE")
        self.assertEqual(result["final_status_message"], "")


if __name__ == "__main__":
    unittest.main()
