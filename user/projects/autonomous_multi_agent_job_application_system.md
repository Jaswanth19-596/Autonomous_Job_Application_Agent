
**Job Application Agent — AI-Powered Job Search and Application Automation**

**Technologies:** Python, LangGraph, LangChain, Model Context Protocol (MCP), Playwright, SQLite, Apify, Pydantic, Pandas, OpenPyXL, LaTeX

Designed and built an AI-powered job application system that coordinates the journey from discovering opportunities to preparing tailored resumes, completing application forms, and verifying submission outcomes. The project combines language-model reasoning with browser automation, persistent workflow state, and human intervention to address the complexity of real-world hiring portals.

**Intelligent Workflow Orchestration**
Engineered a stateful LangGraph workflow with dedicated stages for interpreting user requests, dispatching queued jobs, validating opportunities, extracting job requirements, customizing resumes, resolving application URLs, handling authentication, submitting applications, and verifying results. Conditional routing allows the system to respond to application-specific requirements, request assistance, and continue processing the remaining queue.

**Job Discovery and Duplicate Prevention**
Integrated Apify-based job retrieval with a persistent application queue. Implemented URL normalization and stable job identifiers to recognize duplicate postings across tracking links and URL variations. Maintained separate records for pending, applied, and invalid jobs to organize opportunities and avoid redundant processing.

**Role-Specific Resume Generation**
Built a resume customization pipeline that uses job descriptions, candidate experience, and project information to adapt a reference LaTeX resume for each opportunity. The workflow generates customized resume content and compiles PDF documents, connecting application preparation directly to the submission process.

**Context-Aware Application Answers**
Developed a dedicated answer-generation tool that drafts responses to screening questions using the candidate’s profile, customized resume, project details, and previously supplied answers. Added persistent memory with candidate-wide, employer-specific, and job-specific scopes so the system can reuse relevant information without applying every answer indiscriminately.

**Browser Automation and Form Verification**
Integrated MCP-based browser tooling to navigate hiring portals, follow application redirects, handle login flows, populate fields, and upload resumes. Implemented field verification that checks whether entered values persist and attempts to repair stale state in dynamically controlled inputs, addressing cases where a browser action appears successful but the form does not retain the value.

**Human-in-the-Loop Assistance**
Added workflow interruptions for CAPTCHAs, security challenges, and questions requiring user input. The system captures the reason for intervention and routes execution back to the relevant application stage after the user responds, allowing automation and human judgment to work together.

**Evidence-Based Submission Verification**
Implemented a separate verification stage that checks for a matching Gmail application-confirmation message, with an application-page fallback when email confirmation is unavailable. The workflow distinguishes confirmed submissions from incomplete, failed, or blocked applications rather than treating a submit-button click as proof of success.

**Persistent State and Application Tracking**
Used SQLite-backed LangGraph checkpoints to preserve workflow state and support conversation continuity. Built Excel tracking utilities that update individual application records while preserving existing workbook sheets, tables, and formatting. Added structured outputs, retry policies, and diagnostic logging to make workflow decisions and failures easier to investigate.

This project demonstrates practical engineering across agent orchestration, LLM tool use, browser automation, persistent memory, document generation, and external-service integration—with particular attention to verification, recovery, and the unpredictable behavior of real application workflows.
