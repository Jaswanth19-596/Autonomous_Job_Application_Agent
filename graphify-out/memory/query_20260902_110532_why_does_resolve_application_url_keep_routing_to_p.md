---
type: "query"
date: "2026-09-02T11:05:32.775600+00:00"
question: "Why does resolve_application_url keep routing to perform_login after the page says welcome, you are signed in?"
contributor: "graphify"
source_nodes: ["resolve_application_url", "perform_login"]
---

# Q: Why does resolve_application_url keep routing to perform_login after the page says welcome, you are signed in?

## Answer

Expanded from original query via vocab: [applications, url]. The existing graph is stale for the authentication workflow, so source inspection was required. The loop is caused by the structured resolver returning requires_login=true; route_after_resolving_url routes that result to perform_login. A successful login returns to resolve_application_url but only carries actual_job_url, not an authenticated state or login evidence. The resolver reuses its pre-login transcript and starts from job_url, so it can classify the same prior login gate again.

## Source Nodes

- resolve_application_url
- perform_login