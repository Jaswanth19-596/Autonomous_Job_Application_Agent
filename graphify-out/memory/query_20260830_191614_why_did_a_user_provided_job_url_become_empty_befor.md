---
type: "query"
date: "2026-08-30T19:16:14.142289+00:00"
question: "Why did a user-provided job URL become empty before extract_job_details?"
contributor: "graphify"
source_nodes: ["nodes.py", "get_jobs()", "BaseModel"]
---

# Q: Why did a user-provided job URL become empty before extract_job_details?

## Answer

Expanded from original query via vocab: [user, request, routing, model, job, url, fetch, details]. The captured run shows the user supplied an Ashby HTTP URL, but the structured routing model returned the schema default empty job_url. nodes.py trims and forwards that empty field to extract_job_details, whose page.goto then fails. The URL validator helper exists but is not invoked; add an output contract plus a guard that routes missing URLs to CHAT.

## Source Nodes

- nodes.py
- get_jobs()
- BaseModel