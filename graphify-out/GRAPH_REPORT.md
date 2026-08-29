# Graph Report - .  (2026-08-25)

## Corpus Check
- Corpus is ~2,427 words - fits in a single context window. You may not need a graph.

## Summary
- 46 nodes · 68 edges · 11 communities (6 shown, 5 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Job Identity
- Workbook Updates
- Agent Tools
- Workbook Lookup
- Candidate Profile
- Workbook Package
- Job Row Matching
- Project Metadata
- Project Overview

## God Nodes (most connected - your core abstractions)
1. `update_job_row()` - 8 edges
2. `upsert_job_row()` - 8 edges
3. `append_job_rows()` - 8 edges
4. `linkedin_job_id()` - 7 edges
5. `canonical_job_url()` - 6 edges
6. `_applications_sheet()` - 6 edges
7. `get_job_row()` - 6 edges
8. `get_jobs()` - 5 edges
9. `job_identity()` - 5 edges
10. `find_job_rows_by_canonical_url()` - 5 edges

## Surprising Connections (you probably didn't know these)
- `get_jobs()` --calls--> `canonical_job_id()`  [INFERRED]
  tools.py → workbook/job_identity.py
- `get_jobs()` --calls--> `get_job_row()`  [INFERRED]
  tools.py → workbook/jobs_workbook.py
- `get_job_row()` --calls--> `linkedin_job_id()`  [INFERRED]
  workbook/jobs_workbook.py → workbook/job_identity.py
- `_matching_rows_for_job_id()` --calls--> `linkedin_job_id()`  [INFERRED]
  workbook/jobs_workbook.py → workbook/job_identity.py
- `find_job_rows_by_canonical_url()` --calls--> `canonical_job_url()`  [INFERRED]
  workbook/jobs_workbook.py → workbook/job_identity.py

## Import Cycles
- None detected.

## Communities (11 total, 5 thin omitted)

### Community 0 - "Job Identity"
Cohesion: 0.29
Nodes (9): canonical_job_id(), canonical_job_url(), job_identity(), linkedin_job_id(), Stable job identities shared by ingestion and workbook operations., Return the stable numeric posting ID from a LinkedIn job URL., Normalize a job URL while retaining parameters that identify the job.      Linke, Return a stable deduplication key for a queued job record. (+1 more)

### Community 1 - "Workbook Updates"
Cohesion: 0.33
Nodes (9): Any, append_job_rows(), _extend_tables(), _headers(), Update one job by ID without replacing other workbook sheets., Add a job record or refresh its details without replacing the workbook.      URL, Append jobs not already present, preserving dashboard and table styling., update_job_row() (+1 more)

### Community 2 - "Agent Tools"
Cohesion: 0.33
Nodes (4): BaseModel, AgentOutput, get_jobs(), Fetch and search jobs from data/jobs.xlsx with enhanced filtering and pagination

### Community 3 - "Workbook Lookup"
Cohesion: 0.38
Nodes (6): _applications_sheet(), find_job_rows_by_canonical_url(), get_job_row(), Targeted jobs.xlsx mutations that preserve dashboard sheets and formatting., Return one Applications row by ID without loading the entire sheet.      The wor, Return every row matching a normalized URL without mutating the workbook.

## Knowledge Gaps
- **2 isolated node(s):** `jobapplicationagent`, `README`
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_job_row()` connect `Workbook Lookup` to `Job Identity`, `Agent Tools`, `Candidate Profile`?**
  _High betweenness centrality (0.223) - this node is a cross-community bridge._
- **Why does `get_jobs()` connect `Agent Tools` to `Job Identity`, `Workbook Lookup`?**
  _High betweenness centrality (0.208) - this node is a cross-community bridge._
- **Why does `linkedin_job_id()` connect `Job Identity` to `Workbook Lookup`, `Job Row Matching`?**
  _High betweenness centrality (0.133) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `linkedin_job_id()` (e.g. with `get_job_row()` and `_matching_rows_for_job_id()`) actually correct?**
  _`linkedin_job_id()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `canonical_job_url()` (e.g. with `find_job_rows_by_canonical_url()` and `upsert_job_row()`) actually correct?**
  _`canonical_job_url()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `jobapplicationagent`, `Fetch and search jobs from data/jobs.xlsx with enhanced filtering and pagination`, `Local workbook and candidate-profile access helpers.` to the rest of the system?**
  _16 weakly-connected nodes found - possible documentation gaps or missing edges._