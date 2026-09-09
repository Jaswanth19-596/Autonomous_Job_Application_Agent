---
type: "query"
date: "2026-08-31T16:49:40.167074+00:00"
question: "Where should a Jobright top-five collection node connect, and how should it write jobs to the workbook?"
contributor: "graphify"
source_nodes: ["nodes.py", "upsert_job_row()", "jobs_workbook.py"]
---

# Q: Where should a Jobright top-five collection node connect, and how should it write jobs to the workbook?

## Answer

Expanded from original query via graph vocabulary: [fetch, job, jobs, workbook, nodes]. The graph shows the user-request workflow in nodes.py/app.py and workbook writers in workbook/jobs_workbook.py. The Jobright node is routed from the user-request decision, writes five normalized rows through upsert_job_row(), and then returns to collect_user_request.

## Source Nodes

- nodes.py
- upsert_job_row()
- jobs_workbook.py