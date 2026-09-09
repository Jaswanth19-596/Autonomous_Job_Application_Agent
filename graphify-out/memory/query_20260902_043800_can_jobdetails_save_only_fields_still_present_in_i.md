---
type: "query"
date: "2026-09-02T04:38:00.721015+00:00"
question: "Can JobDetails save only fields still present in its Pydantic model without errors?"
contributor: "graphify"
source_nodes: ["jobs_workbook.py", "append_job_rows()", "update_job_row()"]
---

# Q: Can JobDetails save only fields still present in its Pydantic model without errors?

## Answer

Expanded from original query via vocab: [details, model, workbook, rows, add, update, fields]. The graph identified the workbook upsert boundary; save_job_to_workbook now derives its data fields from the current Pydantic model and preserves only required workflow metadata.

## Source Nodes

- jobs_workbook.py
- append_job_rows()
- update_job_row()