Autonomous Multi-Agent Job Application System [GitHub Repository]
Python, LangGraph, MCP, Playwright, OpenRouter, Apify
• Engineered a hierarchical multi-agent framework using LangGraph to automate end-to-end job applications, implementing a
sequential Manager-Worker state machine with deterministic fallback for CAPTCHAs and scam detection.
• Reduced LLM token overhead by implementing an active memory-management pipeline that prunes heavy DOM payloads
from context windows beyond 18 conversation turns while preserving critical action history.
• Integrated MCP-based browser and Gmail tooling with asynchronous session management, dynamic tool discovery, timeout
handling, graceful server recovery, and human-in-the-loop interaction for unknown application questions.
• Built a resilient tool-execution runtime with human-in-the-loop approval gates for high-risk actions, automatic exception
recovery, and asynchronous MCP tool resolution