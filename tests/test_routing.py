import unittest
from unittest.mock import patch
from utilities.routing import route_after_login
from nodes import human_in_the_loop, Overwrite


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_route_after_login_success(self):
        state = {
            "login_phase": "SUCCESS",
            "login_return_node": "resolve_application_url"
        }
        self.assertEqual(route_after_login(state), "resolve_application_url")

    def test_route_after_login_needs_tools(self):
        state = {
            "login_phase": "NEEDS_TOOLS"
        }
        self.assertEqual(route_after_login(state), "tool_node")

    def test_route_after_login_requires_human(self):
        state = {
            "login_phase": "REQUIRES_HUMAN"
        }
        self.assertEqual(route_after_login(state), "human_in_the_loop")

    def test_route_after_login_failed_goes_to_update_job_status(self):
        state = {
            "login_phase": "FAILED",
            "requires_human": False
        }
        self.assertEqual(route_after_login(state), "update_job_status")

    def test_route_after_login_next_step(self):
        state = {
            "next_step": "update_job_status",
            "login_phase": "FAILED"
        }
        self.assertEqual(route_after_login(state), "update_job_status")

    @patch("nodes.interrupt")
    async def test_human_in_the_loop_continue_next_app(self, mock_interrupt):
        mock_interrupt.return_value = "continue with the next application"
        state = {
            "human_intervention_origin": "perform_login",
            "human_intervention_prompt": "Login failed issue",
            "human_intervention_reason": "OTHER",
        }
        res = await human_in_the_loop(state)
        self.assertEqual(res["next_step"], "update_job_status")
        self.assertEqual(res["login_phase"], "FAILED")
        self.assertEqual(res["final_status_message"], "Login failed")
        self.assertEqual(res["application_status"], "Not Applied")

    @patch("nodes.interrupt")
    async def test_resolver_move_on_preserves_outcome(self, mock_interrupt):
        cases = [
            ("Yes mark as applied and move on dude", {}, "Applied"),
            ("move on", {"job_application_phase": "APPLIED", "application_status": "Not Applied"}, "Applied"),
            ("skip", {"is_applied": True}, "Applied"),
            ("continue with the next application", {}, "Not Applied"),
            ("move on", {"application_status": "Failed"}, "Failed"),
            ("mark as not applied and move on", {"is_applied": True}, "Not Applied"),
            ("mark as in progress and move on", {}, "In Progress"),
        ]
        for reply, outcome, expected in cases:
            with self.subTest(reply=reply, outcome=outcome):
                mock_interrupt.return_value = reply
                result = await human_in_the_loop({
                    "human_intervention_origin": "resolve_application_url",
                    "human_intervention_prompt": "Verify the existing application",
                    "human_intervention_reason": "OTHER",
                    **outcome,
                })
                self.assertEqual(result["next_step"], "update_job_status")
                self.assertEqual(result["application_status"], expected)
                self.assertEqual(result["is_applied"], expected == "Applied")
                self.assertFalse(result["requires_human"])
                self.assertEqual(result["human_intervention_origin"], "")
                self.assertIn(reply, result["final_status_message"])

    @patch("nodes.interrupt")
    async def test_resolver_retry_still_returns_to_resolver(self, mock_interrupt):
        mock_interrupt.return_value = "I fixed the redirect, try again"
        result = await human_in_the_loop({
            "human_intervention_origin": "resolve_application_url",
            "human_intervention_prompt": "Redirect failed",
            "human_intervention_reason": "OTHER",
        })
        self.assertEqual(result["next_step"], "resolve_application_url")
        self.assertEqual(result["resolve_application_url_messages"], [
            {"role": "user", "content": mock_interrupt.return_value},
        ])

    @patch("nodes.interrupt")
    async def test_human_in_the_loop_retry_login(self, mock_interrupt):
        mock_interrupt.return_value = "I logged in manually, try again"
        state = {
            "human_intervention_origin": "perform_login",
            "human_intervention_prompt": "Login failed issue",
            "human_intervention_reason": "OTHER",
        }
        res = await human_in_the_loop(state)
        self.assertEqual(res["next_step"], "perform_login")
        self.assertEqual(res["perform_login_messages"], [{"role": "user", "content": "I logged in manually, try again"}])


if __name__ == "__main__":
    unittest.main()
