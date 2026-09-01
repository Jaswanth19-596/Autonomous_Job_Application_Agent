import unittest

from utilities.routing import route_user_request


class JobrightRoutingTests(unittest.TestCase):
    def test_routes_jobright_action_to_the_dedicated_node(self):
        self.assertEqual(
            route_user_request(
                {"next_step": "FETCH_JOBRIGHT_JOBS", "next_action": "FETCH_JOBRIGHT_JOBS"}
            ),
            "fetch_jobright_jobs",
        )


if __name__ == "__main__":
    unittest.main()
