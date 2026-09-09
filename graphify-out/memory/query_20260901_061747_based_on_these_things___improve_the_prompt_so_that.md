---
type: "query"
date: "2026-09-01T06:17:47.077297+00:00"
question: "Based on these things,, improve the prompt so that the model would use the better ways to fill the job applications"
contributor: "graphify"
source_nodes: ["nodes.py", "UserProfile", "tools.py"]
---

# Q: Based on these things,, improve the prompt so that the model would use the better ways to fill the job applications

## Answer

Expanded from original query via vocab: [agent, tools, operations, applications, job, model, profile, user]. Updated prompts/apply_for_job/system.txt with a snapshot-first tool hierarchy, page-level planning, batched fill_form calls, a deterministic custom combobox procedure, visible-row handling, and bounded evaluate_script fallback rules. Each unresolved widget now permits at most one narrow diagnostic script and, only when no normal UID exists, one final targeted action script followed by snapshot verification. Preserved the existing source-of-truth, Simplify, upload, human-intervention, and submission safeguards. Prompt loading and diff validation passed; the pytest suite could not run because pytest is not installed in either the uv or system environment.

## Source Nodes

- nodes.py
- UserProfile
- tools.py