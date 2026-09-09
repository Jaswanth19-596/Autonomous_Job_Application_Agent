---
type: "query"
date: "2026-08-31T22:34:00.057635+00:00"
question: "Why does the agent say it gave four options after get_jobs returned five records, without displaying the options?"
contributor: "graphify"
source_nodes: ["get_jobs()", "user_request_llm_node", "UserRequestRoutingDecision"]
---

# Q: Why does the agent say it gave four options after get_jobs returned five records, without displaying the options?

## Answer

Expanded from original query via vocab: [user, job, jobs]. The tool trace proves get_jobs returned returned_count=5, while the printed structured message says 'I gave you 4 solid options above' and contains no entries. user_request_llm_node prints structured_response.message correctly. The LLM was given the ToolMessage but the prompt only permits tool use and gives no hard output contract requiring the message to enumerate every returned job or match returned_count. This is an ungrounded LLM response. Require an exact, enumerated message format and forbid references such as 'above' unless entries appear in the current message; for a reliable guarantee, return structured job rows and render them in Python.

## Source Nodes

- get_jobs()
- user_request_llm_node
- UserRequestRoutingDecision