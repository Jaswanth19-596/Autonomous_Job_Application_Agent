
import logging


logger = logging.getLogger(__name__)


def condition(state):
    if state["next_action"] == "CHAT":
        logger.debug("Routing to chat_with_user")
        return "chat_with_user"
    if state['next_action'] == "FETCH_JOB_DESCRIPTION":
        logger.debug("Routing to fetch_job_description")
        return "fetch_job_description"
    if state['next_action'] == "END":
        logger.debug("Routing to END")
        return "END"

def login_condition(state):
    if state["requires_login"] == True:
        return "perform_login"
    else:
        return "apply_for_job"
