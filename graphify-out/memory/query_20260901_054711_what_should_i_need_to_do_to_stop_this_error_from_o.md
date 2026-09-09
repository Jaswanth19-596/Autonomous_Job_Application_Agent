---
type: "query"
date: "2026-09-01T05:47:11.243686+00:00"
question: "What should i need to do to stop this error from occuring ?"
contributor: "graphify"
source_nodes: ["nodes.py", "tools.py", "_applications_sheet()", "BaseModel"]
---

# Q: What should i need to do to stop this error from occuring ?

## Answer

Expanded from original query via graph vocabulary: [applications, model, tools]. The TLS failure cannot be completely prevented, but the workflow can be made resilient. Add a targeted retry around model ainvoke calls for ssl.SSLError and httpx.TransportError with exponential backoff and jitter. Retry only the model request, never the browser submission tool. Persist a submission-confirmed checkpoint immediately when Workday shows Application Submitted so a later model or network failure cannot cause resubmission; on recovery route to verification or status update. Configure a realistic timeout in milliseconds, such as request_timeout=120000, because ChatOpenRouter maps this value to timeout_ms; max_retries=2 is already the local library default but does not catch the raw SSL error observed. Upgrade the OpenRouter and HTTP transport packages after compatibility testing, and rule out VPN, proxy, TLS-inspection, or unstable Wi-Fi if the error repeats.

## Source Nodes

- nodes.py
- tools.py
- _applications_sheet()
- BaseModel