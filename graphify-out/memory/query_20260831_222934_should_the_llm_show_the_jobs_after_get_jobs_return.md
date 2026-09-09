---
type: "query"
date: "2026-08-31T22:29:34.277536+00:00"
question: "Should the LLM show the jobs after get_jobs returns them, rather than only say it found them?"
contributor: "graphify"
source_nodes: ["get_jobs()", "UserRequestRoutingDecision", "user_request_llm_node"]
---

# Q: Should the LLM show the jobs after get_jobs returns them, rather than only say it found them?

## Answer

Expanded from original query via vocab: [user, job, jobs]. Yes. The run shows the LLM received the tool result because it reports the exact total (416), but the current prompt explicitly calls message a short confirmation/follow-up and never requires listing returned rows. The print statement is correct; the response contract is wrong for the intended behavior. Require the CHAT message after get_jobs to enumerate every returned job with title, company, location, posted date, and link, and change the message schema description from short confirmation to a user-facing reply that includes those entries.

## Source Nodes

- get_jobs()
- UserRequestRoutingDecision
- user_request_llm_node