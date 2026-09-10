---
type: "query"
date: "2026-08-31T22:27:24.285528+00:00"
question: "Why does requesting jobs from Excel show a summary instead of the jobs? Is understand_user_request_node printing the wrong thing?"
contributor: "graphify"
source_nodes: ["get_jobs()", "understand_user_request_node", "tool_node"]
---

# Q: Why does requesting jobs from Excel show a summary instead of the jobs? Is understand_user_request_node printing the wrong thing?

## Answer

Expanded from original query via vocab: [user, job, jobs]. The get_jobs tool returns 10 records, and tool_node logs that raw result. On the next pass, understand_user_request_node prints structured_response.message, which is only the UserRequestRoutingDecision user-facing summary and does not contain a jobs list. The actual records remain ToolMessages in state['messages']; log_tool_result also truncates terminal output at 2,000 characters. To display jobs reliably, format the tool result in Python or add structured job rows to the response schema rather than relying on the LLM summary message.

## Source Nodes

- get_jobs()
- understand_user_request_node
- tool_node