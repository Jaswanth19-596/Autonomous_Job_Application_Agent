import unittest

from utilities.routing import (
    route_after_validation,
    route_after_login,
    route_after_resolving_url,
    route_user_request,
    submit_application_decision,
)


class JobrightRoutingTests(unittest.TestCase):
    def test_routes_eligible_job_to_the_combined_url_resolution_node(self):
        self.assertEqual(
            route_after_validation({"validation_phase": "ELIGIBLE"}),
            "resolve_application_url",
        )

    def test_routes_jobright_action_to_the_dedicated_node(self):
        self.assertEqual(
            route_user_request(
                {"next_step": "JOBRIGHT_JOBS", "next_action": "JOBRIGHT_JOBS"}
            ),
            "fetch_jobright_jobs",
        )

    def test_routes_an_unsuccessful_application_without_a_human_blocker_to_status_update(self):
        self.assertEqual(
            submit_application_decision(
                {"job_application_phase": "NOT_APPLIED", "requires_human": False}
            ),
            "update_job_status",
        )

    def test_routes_an_unsuccessful_application_with_a_human_blocker_to_review(self):
        self.assertEqual(
            submit_application_decision(
                {"job_application_phase": "NOT_APPLIED", "requires_human": True}
            ),
            "human_in_the_loop",
        )

    def test_routes_a_resolved_login_gate_to_the_login_node(self):
        self.assertEqual(
            route_after_resolving_url({"resolution_phase": "REQUIRES_LOGIN"}),
            "perform_login",
        )

    def test_routes_successful_login_back_to_the_node_that_requested_it(self):
        self.assertEqual(
            route_after_login(
                {"login_phase": "SUCCESS", "login_return_node": "resolve_application_url"}
            ),
            "resolve_application_url",
        )
        self.assertEqual(
            route_after_login(
                {"login_phase": "SUCCESS", "login_return_node": "submit_job_application"}
            ),
            "submit_job_application",
        )

    def test_routes_submission_login_gate_to_the_login_node(self):
        self.assertEqual(
            submit_application_decision(
                {"job_application_phase": "REQUIRES_LOGIN", "requires_human": False}
            ),
            "perform_login",
        )


if __name__ == "__main__":
    unittest.main()
