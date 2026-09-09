---
type: "query"
date: "2026-09-01T06:07:20.828371+00:00"
question: "I want you to analyze the logs and tell me for doing what the agent is using evaluate script so many times ? It can simply take the snapshot and fill the fields right, but why is it taking so many evaluate script tool calls"
contributor: "graphify"
source_nodes: ["nodes.py", "tools.py", "AgentOutput"]
---

# Q: I want you to analyze the logs and tell me for doing what the agent is using evaluate script so many times ? It can simply take the snapshot and fill the fields right, but why is it taking so many evaluate script tool calls

## Answer

Expanded from original query via vocab: [agent, tools, operations, applications, job, data, source]. The trace has 9 evaluate_script calls: 2 inspect or set personal fields, 4 inspect education DOM and visibility, and 3 inspect or operate the Select2 school autocomplete. Most of the repetition is trial-and-error around a hidden 1x1 Select2 control after a failed type_text attempt. A custom autocomplete can require click, type, wait, inspect, and select events, but the three visibility probes and direct LinkedIn DOM setter were avoidable. The architecture gives the model all Chrome tools and routes every tool response back through a new model turn, while the prompt does not establish evaluate_script as a last resort, so the model chose iterative DOM debugging. The final failure was SSLV3_ALERT_BAD_RECORD_MAC after the option click, not a browser-form validation error.

## Source Nodes

- nodes.py
- tools.py
- AgentOutput