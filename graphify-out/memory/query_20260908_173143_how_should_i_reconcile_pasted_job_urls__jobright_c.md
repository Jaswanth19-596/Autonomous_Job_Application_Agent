---
type: "query"
date: "2026-09-08T17:31:43.347343+00:00"
question: "How should I reconcile pasted job URLs, Jobright collection, and scripts fetching latest jobs into one application workflow?"
contributor: "graphify"
source_nodes: ["jobs_workbook.py", "job_identity.py", "get_jobs"]
---

# Q: How should I reconcile pasted job URLs, Jobright collection, and scripts fetching latest jobs into one application workflow?

## Answer

Expanded via graph vocabulary: fetch jobs ingestion queued applications agent url deduplication. Graph evidence shows workbook/jobs_workbook.py provides append_job_rows, upsert_job_row and update_job_row; workbook/job_identity.py provides shared job identities; tools.py get_jobs searches data/applied_jobs.xlsx. Recommendation, not verified current behavior: use three source collectors feeding one durable job inbox, with explicit collect, review and apply intentions, shared deduplication, persistent per-job progress, bounded batches and resume. Source selection should be independent of action selection.

## Source Nodes

- jobs_workbook.py
- job_identity.py
- get_jobs