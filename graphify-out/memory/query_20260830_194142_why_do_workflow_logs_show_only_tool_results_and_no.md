---
type: "query"
date: "2026-08-30T19:41:42.944113+00:00"
question: "Why do workflow logs show only tool results and not whether the agent is stuck?"
contributor: "graphify"
source_nodes: ["nodes.py", "get_jobs()"]
---

# Q: Why do workflow logs show only tool results and not whether the agent is stuck?

## Answer

Expanded from original query via vocab: [nodes, tools, output, user, job]. utilities/diagnostics.py defines log_node_stage as a no-op, so every node start, completion, and routing log is suppressed. nodes.py tool_node calls log_tool_result but never log_tool_calls, and every green panel uses tool_execution_node rather than the original requesting node. The captured tool sequence proves the agent was active: it listed pages, selected a page, navigated to the Ashby application, took snapshots, clicked, waited for Simplify autofill, and filled the form.

## Source Nodes

- nodes.py
- get_jobs()