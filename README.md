OpenCode Go requests identify this application as `jobapplicationagent/0.1.0`
and send the LangGraph conversation's `configurable.thread_id` in
`x-opencode-session` on tool and structured-output calls.

`app.py` creates a new thread ID for each run. Set `JOB_AGENT_THREAD_ID` to the
existing ID when continuing a conversation. LangGraph Studio and other graph
callers must provide a stable `configurable.thread_id`; use a different ID for
each new conversation. IDs must contain printable ASCII characters without
spaces. Direct callers of the model factories in `nodes.py` can pass
`config={"configurable": {"thread_id": "your-conversation-id"}}` explicitly.
