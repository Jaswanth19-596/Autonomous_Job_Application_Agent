"""Request identity for OpenCode Go inference calls."""

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import ensure_config


OPENCODE_USER_AGENT = "jobapplicationagent/0.1.0"


def opencode_session_headers(config: RunnableConfig | None = None) -> dict[str, str]:
    """Use the current graph conversation ID, including after checkpoint resume.

    LangChain propagates the runnable config through task-local context, so
    concurrent conversations can share an HTTP client without sharing headers.
    Standalone callers must supply the same thread ID for each conversation.
    """
    thread_id = ensure_config(config)["configurable"].get("thread_id")
    session_id = str(thread_id) if thread_id is not None else ""
    if not session_id or any(not 33 <= ord(char) <= 126 for char in session_id):
        raise ValueError(
            "OpenCode Go requires a nonempty, printable ASCII thread_id without "
            "spaces in config['configurable']; reuse it for the conversation."
        )
    return {"x-opencode-session": session_id}
