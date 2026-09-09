---
type: "query"
date: "2026-08-31T22:36:21.206757+00:00"
question: "Why define structured output in both the system prompt and the Pydantic model?"
contributor: "graphify"
source_nodes: ["UserRequestRoutingDecision", "user_request_llm_node"]
---

# Q: Why define structured output in both the system prompt and the Pydantic model?

## Answer

Expanded from original query via vocab: [user, job, jobs]. In this code, get_custom_structured_model calls model.with_structured_output(UserRequestRoutingDecision), so the Pydantic model supplies the JSON shape, required fields, literal enum, and field descriptions to the model and validates the parsed result. The system prompt should contain behavioral rules that types cannot validate: when each route is appropriate, tool policy, and the requirement to list returned jobs. Repeating field names and types in the prompt is redundant and risks drift; remove the structured-output contract list from the prompt, keeping only behavior.

## Source Nodes

- UserRequestRoutingDecision
- user_request_llm_node