---
type: "query"
date: "2026-09-02T04:03:02.342986+00:00"
question: "I see that fetch_jobright_jobs is out of my coding style and workbook insertion should be independent of the node."
contributor: "graphify"
source_nodes: ["nodes.py", "tools.py", "jobs_workbook.py", "upsert_job_row()"]
---

# Q: I see that fetch_jobright_jobs is out of my coding style and workbook insertion should be independent of the node.

## Answer

Expanded from original query via vocab: [fetch, job, jobs, nodes, tools, workbook, update, sheets]. The graph identified the existing tool-node and workbook helper boundaries; the Jobright flow was refactored to use them.

## Source Nodes

- nodes.py
- tools.py
- jobs_workbook.py
- upsert_job_row()