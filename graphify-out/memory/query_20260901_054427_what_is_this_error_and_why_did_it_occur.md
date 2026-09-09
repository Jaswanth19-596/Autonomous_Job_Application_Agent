---
type: "query"
date: "2026-09-01T05:44:27.737019+00:00"
question: "What is this error and why did it occur?"
contributor: "graphify"
source_nodes: ["nodes.py", "BaseModel"]
---

# Q: What is this error and why did it occur?

## Answer

Expanded from original query via graph vocabulary: [applications, model]. The Workday page confirmed Application Submitted before the failure. The failing operation was a later OpenRouter model request from submit_job_application. TLS record integrity failed while receiving response headers with SSLV3_ALERT_BAD_RECORD_MAC, so no model response arrived. This is a transport-layer SSL error, most likely a transient damaged connection or interference from a proxy, VPN, firewall, or remote endpoint, rather than a rejected job submission or form error. The configured SDK retry path did not recover it.

## Source Nodes

- nodes.py
- BaseModel