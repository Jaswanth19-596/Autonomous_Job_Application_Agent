# MedBot — source-of-truth project brief

**Purpose:** This document is the canonical, evidence-based input for generating resume bullets, portfolio descriptions, cover-letter paragraphs, and interview answers about MedBot. Use only the supported claims below. Do not infer production scale, clinical safety, user adoption, ownership, or business impact.

**Project type:** Portfolio medical-information chatbot using retrieval-augmented generation (RAG).

**Repository scope:** Python application with a Streamlit interface, an OpenAI/Pinecone retrieval workflow, 
offline PDF indexing, RAGAS evaluation, Docker packaging, and a GitHub Actions deployment workflow.

Github: https://github.com/Jaswanth19-596/MedBot-A-Medical-Chatbot

## One-line description

Built a medical-information RAG chatbot that retrieves and filters content from indexed medical PDFs, streams source-cited responses in Streamlit, and was evaluated on 129 ground-truth questions.

## Verified technical summary

MedBot indexes text from local PDFs into Pinecone with OpenAI embeddings. At question time, a LangChain agent invokes a retrieval tool that rewrites the question, retrieves 10 semantically similar chunks, filters the chunks with a structured relevance check, and returns text containing source-book and page metadata to the agent. The Streamlit UI streams status updates and answer tokens, keeps session chat state, and applies a SQLite-backed per-IP request limit of five requests per 30 minutes.

The project uses `gpt-4o-mini` for the configured chat model and `text-embedding-3-small` at 700 dimensions. The evaluation workflow retrieves 10 chunks, reranks the results to 3 with Cohere, then measures five RAGAS metrics against 129 ground-truth questions.

## Problem and user value

| Supported statement | Evidence | Safe framing |
| --- | --- | --- |
| The project makes a PDF-based medical corpus searchable through a chat UI. | `src/data_ingestion.py`, `src/data_indexing.py`, `app.py` | "Built a searchable medical-information chatbot." |
| Responses are designed to be grounded in retrieved material and include book/page citations. | `src/agent.py` system prompt and `retrieve_context` serialization | "Designed the retrieval flow to pass source and page metadata into responses." |
| The UI surfaces an informational-use disclaimer. | `app.py` | "Included an informational-use medical disclaimer." |
| The application manages basic request volume per client IP. | `rate_limit.py`, `config/config.yaml` | "Implemented a local SQLite-backed per-IP rate limit." |

Do not say the system provides medical advice, diagnoses patients, improves health outcomes, is clinically validated, HIPAA compliant, or safe for real-world clinical use. The repository does not establish any of those claims.

## Architecture and implementation decisions

| Decision | What was implemented | Why it is reasonable to discuss |
| --- | --- | --- |
| RAG over a local PDF corpus | Recursive PDF discovery, metadata cleanup, chunking, embedding, and Pinecone storage. | Separates knowledge retrieval from model generation and retains citation metadata. |
| Query rewriting before retrieval | An LLM rewrites the user question before the Pinecone search. | Intended to make questions more specific and searchable. |
| Two-stage context selection | Similarity retrieval returns 10 candidates; the application then uses an LLM relevance check per candidate. | Adds a relevance-filtering step before the agent receives context. |
| Cited context | Each retained chunk is serialized with `book_name` and zero-based PDF page number. | Supports traceability to the indexed source material. |
| Stateful agent history | `InMemorySaver` is paired with conversation summarization after 10 messages, retaining 3. | Controls in-process conversation growth; it is not durable storage. |
| Streaming UI | Streamlit consumes both custom retrieval-status updates and model message tokens. | Gives users intermediate retrieval feedback and progressive responses. |
| Local rate limiting | SQLite records request timestamps by IP, configured as 5 requests per 1,800 seconds. | Provides basic cost/abuse control for one deployment instance. |
| Repeatable packaging/deployment | A multi-stage Docker build and GitHub Actions workflow build and push an ECR image, then deploy it to EC2. | Demonstrates containerization and an automated deployment path. |

## Technology inventory

| Area | Technologies verified in the repository |
| --- | --- |
| Language and UI | Python, Streamlit |
| LLM and RAG orchestration | OpenAI, LangChain, LangGraph |
| Retrieval | OpenAI `text-embedding-3-small`, Pinecone, similarity search |
| Document processing | LangChain `DirectoryLoader`, `PyPDFLoader`, `RecursiveCharacterTextSplitter` |
| Evaluation | RAGAS, Hugging Face `datasets`, Cohere reranking |
| Persistence and observability | SQLite request-limit store; file logging; optional LangSmith environment variables in deployment |
| Packaging and delivery | Docker, GitHub Actions, Amazon ECR, EC2 |

## Measurable, reproducible outcomes

These results are calculated from the checked-in `ragas_evaluation_results.csv`. They describe one recorded evaluation dataset and configuration—not a production benchmark or clinical validation.

| Metric | Mean | Dataset scope |
| --- | ---: | --- |
| Context recall | 0.910 | 129 ground-truth questions |
| Context precision | 0.924 | 129 ground-truth questions |
| Faithfulness | 0.890 | 129 ground-truth questions |
| Answer relevancy | 0.904 | 129 ground-truth questions |
| Answer correctness | 0.697 | 129 ground-truth questions |

Safe outcome phrasing:

- "Evaluated the RAG workflow on 129 curated questions; the checked-in run recorded 0.924 context precision and 0.890 faithfulness."
- "Measured five RAGAS metrics, identifying answer correctness (0.697) as lower than retrieval and grounding metrics."

Do not convert the scores into claims of 91% medical accuracy, 89% factual correctness in general, reduced hallucinations in production, or comparison-to-industry performance. The metrics are dataset- and methodology-specific.

## Claim boundaries

### Supported claims

- Built or implemented the components described in this brief, if the candidate was responsible for the repository work.
- Configured a retrieval pipeline with 800-character chunks, 40-character overlap, 10 initial results, and 3 reranked evaluation results.
- Used OpenAI, Pinecone, LangChain/LangGraph, Streamlit, RAGAS, Cohere, SQLite, Docker, GitHub Actions, ECR, and EC2 as documented above.
- Evaluated 129 questions with the recorded metric means.
- Added source/page metadata to context supplied to the agent.

### Claims requiring external confirmation before use

- The candidate’s exact role, ownership percentage, team size, dates, hours, and leadership responsibilities.
- Whether the GitHub Actions deployment has run successfully in a live AWS environment.
- Real users, active users, traffic, latency, availability, cost, or uptime.
- Source corpus size, number of PDFs/pages/chunks, or Pinecone vector count.
- Security posture, privacy compliance, HIPAA status, clinical review, or accessibility testing.
- Any improvement over a baseline, unless a dated experiment and its baseline are provided.

### Known implementation limitations

- The user-facing medical disclaimer and grounded-answer prompt are safeguards, not a clinical safety system.
- The rate limiter uses a local SQLite database, so rate-limit state is not shared across instances and may be ephemeral in the Docker deployment without a mounted volume.