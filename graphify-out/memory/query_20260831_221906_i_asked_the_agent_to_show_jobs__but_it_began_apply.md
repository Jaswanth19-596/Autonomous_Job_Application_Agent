---
type: "query"
date: "2026-08-31T22:19:06.306948+00:00"
question: "I asked the agent to show jobs, but it began applying. How did understand_user_request_node reach is_job_valid without a direct edge?"
contributor: "graphify"
source_nodes: ["understand_user_request_node", "route_user_request", "prepare_job_application_queue", "dispath_job", "build_validation_messages", "is_job_valid"]
---

# Q: I asked the agent to show jobs, but it began applying. How did understand_user_request_node reach is_job_valid without a direct edge?

## Answer

Expanded from original query via vocab: [user, job, jobs]. The current graph extraction does not include app.py or nodes.py, so it cannot trace this workflow. Direct code inspection shows this path: understand_user_request_node -> route_user_request (APPLY_FOR_JOBS) -> prepare_job_application_queue -> dispath_job -> route_after_dispatch -> build_validation_messages -> is_job_valid. The two queue nodes and conditional routing helpers do not use trace_node, so the terminal does not print them. The log message 'Applying to the 5 newest saved jobs' is the structured router's message and demonstrates a misclassification; the chat prompt says list/show requests should be CHAT.

## Source Nodes

- understand_user_request_node
- route_user_request
- prepare_job_application_queue
- dispath_job
- build_validation_messages
- is_job_valid