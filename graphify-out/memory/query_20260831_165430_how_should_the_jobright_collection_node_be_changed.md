---
type: "query"
date: "2026-08-31T16:54:30.357463+00:00"
question: "How should the Jobright collection node be changed so the LLM controls the browser?"
contributor: "graphify"
source_nodes: ["nodes.py", "tools.py", "jobs_workbook.py"]
---

# Q: How should the Jobright collection node be changed so the LLM controls the browser?

## Answer

Expanded from graph vocabulary: [jobright, fetch, browser, tools, node]. The Jobright node now starts the project's Chrome MCP tools, repeatedly lets the LLM invoke them to navigate and inspect Jobright, and only then creates the five structured workbook rows. The browser endpoint flag was corrected to --browserUrl so Chrome DevTools MCP can attach.

## Source Nodes

- nodes.py
- tools.py
- jobs_workbook.py