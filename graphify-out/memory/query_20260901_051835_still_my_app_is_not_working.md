---
type: "query"
date: "2026-09-01T05:18:35.903395+00:00"
question: "Still my app is not working"
contributor: "graphify"
source_nodes: ["app.py", "nodes.py"]
---

# Q: Still my app is not working

## Answer

Expanded from original query via vocab: [app, model, nodes, main]. The graph identifies app.py as the workflow entry point and nodes.py as the model-node module. Runtime reproduction found that ChatOpenRouter forwards request_timeout to the OpenRouter SDK in milliseconds; nodes.py configured 120.0 ms, so regular DeepSeek calls timed out. Changed it to 120,000 ms. The corrected async call returned hello, and app.py reached the collect_user_request prompt.

## Source Nodes

- app.py
- nodes.py