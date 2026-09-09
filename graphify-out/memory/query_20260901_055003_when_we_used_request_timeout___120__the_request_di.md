---
type: "query"
date: "2026-09-01T05:50:03.682780+00:00"
question: "When we used request_timeout = 120, the request did not go and the model did not respond and there was no message, it just kept hanging. What is the reason?"
contributor: "graphify"
source_nodes: ["nodes.py", "BaseModel"]
---

# Q: When we used request_timeout = 120, the request did not go and the model did not respond and there was no message, it just kept hanging. What is the reason?

## Answer

Expanded from original query via graph vocabulary: [model]. In the installed langchain-openrouter version, request_timeout is an integer measured in milliseconds and is passed to the OpenRouter SDK as timeout_ms. Therefore request_timeout=120 means 120 milliseconds, not 120 seconds. The library defaults max_retries to 2 and configures retry_connection_errors=True with up to about 300 seconds total retry time. A 120-millisecond request therefore times out repeatedly inside the SDK while the application progress wrapper only shows its spinner, making it appear hung. Use request_timeout=120_000 for 120 seconds; during debugging set max_retries=0 to surface the timeout immediately, or add an explicit application-level deadline and retry logging.

## Source Nodes

- nodes.py
- BaseModel