


# Agentic Content Writing System

A production-minded content pipeline that uses a CrewAI crew to research, draft, and edit a source-backed Markdown article. It makes the handoffs explicit, validates every machine-readable contract with Pydantic, retries invalid outputs through CrewAI guardrails, and saves reusable memory to SQLite.

Github: https://github.com/Jaswanth19-596/Agentic_content_writing_system

```text
Content brief
    │
    ▼
Researcher ──ResearchBrief──► Writer ──Markdown draft──► Editor ──FinalArticle──► Markdown + JSON
    │                              │                         │
    └── web + persisted memory     └── prior task context      └── deterministic guardrail + retry
```

## What is implemented

- **Specialist crew:** evidence-first Researcher, audience-focused Writer, and Standards Editor. `--mode sequential` uses explicit task context; `--mode hierarchical` adds a delegating managing editor.
- **ReAct-style tool use:** the Researcher can decide when to call narrowly scoped live-web and persisted-memory tools. Agents have bounded iterations and internal planning enabled; private chain-of-thought is never requested or persisted.
- **Pydantic contracts:** `ContentBrief`, `ResearchBrief`, `FinalArticle`, and memory records define each input/handoff.
- **Guardrails and retries:** research must have source-backed findings with sufficient confidence. A final article must pass schema, exact word bounds, citation-link, slug, meta-description, and keyword checks before it can be written.
- **Four explicit memory scopes:** short-term run handoffs, long-term prior work, entity/topic knowledge, and contextual run notes live in a queryable SQLite database. Relevant records are injected into later research runs as potentially stale context to reduce repeated retrieval.
- **Auditable outputs:** each published article produces `outputs/<slug>.md` plus `outputs/<slug>.json`; memory can be inspected from the CLI.

## Setup

Requires Python 3.11+ and an OpenAI API key. Tavily is optional but enables live research.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env`. Optionally set `TAVILY_API_KEY` to give the Researcher web-search capability. When Tavily is absent, provide `--source-url` values or expect the research task to state that claims cannot be verified.

## Generate an article

```bash
agentic-writer generate \
  --topic "How multi-agent systems improve content quality" \
  --audience "engineering leaders evaluating AI workflows" \
  --purpose "publish a practical technical explainer" \
  --keyword "multi-agent system" \
  --keyword "content quality" \
  --source-url "https://docs.crewai.com/en/concepts/tasks" \
  --source-url "https://docs.crewai.com/en/concepts/processes" \
  --min-words 750 \
  --max-words 1000 \
  --mode sequential
```

For manager-led task delegation, replace `--mode sequential` with `--mode hierarchical`. The manager uses `ACW_MANAGER_LLM`; specialist agents use `ACW_LLM`.

```bash
agentic-writer memory "multi-agent content"
pytest
ruff check .
```

## Design notes

CrewAI’s task context passes the structured research output into drafting and then into editing. Function guardrails return `(valid, result-or-feedback)`, so failed checks produce actionable feedback and retry the responsible task up to `ACW_MAX_GUARDRAIL_RETRIES` times. The separate SQLite store is intentional: framework memory is enabled for within-crew context, while application memory remains inspectable and portable across runs. CrewAI's own storage is redirected into `data/crewai/`, avoiding hidden user-profile state.

The live-search tool returns a graceful “do not invent sources” response if its optional provider is unconfigured or unavailable. It is deliberately not a general network executor.

## Project layout

```text
src/agentic_content_writer/
  cli.py          # generate and memory-inspection commands
  config.py       # environment settings and secret validation
  crew.py         # agents, tasks, orchestration, persistence
  guardrails.py   # deterministic retry conditions
  memory.py       # explicit SQLite memory scopes
  schemas.py      # Pydantic data contracts
  tools.py        # narrow ReAct tools
tests/            # unit tests for reliability layer
```

Example: 
Agentic Content Writing System | Python, Crew AI, OpenAI API, Pydantic
• Built a production grade Multi Agent content writing system, evolving from a single agent baseline to a fully orchestrated, multi-role
crew applying all core agentic AI patterns in a unified implementation.
• Implemented the ReAct(Reasoning + Acting) pattern enabling agents to reason step-by-step, dynamically select tools and self-correct
before producing outputs, reducing hallucinations and improving task reliability.
• Designed multi-agent crew with specialized roles(Researcher, Writer and Editor) using hierarchical and sequential process modes with
inter-agent task-delegation and context passing between crew members.
• Implemented guardrails and validation layers to intercept malformed outputs, trigger retry logic, and enforce editorial constraints like
word count bounds before output propagation.
• Managed short-term, long-term, entity and contextual memory across agent interactions, enabling the crew to retain context across tasks
and reduce redundant information retrieval.