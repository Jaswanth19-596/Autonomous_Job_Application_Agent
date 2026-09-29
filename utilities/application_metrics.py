"""Per-application metrics accumulator for the job-application workflow.

Every application cycle (dispatch → … → update_job_status) shares a single
metrics dict that nodes update as they run.  The dict is stored in
``state["application_metrics"]`` and serialised through LangGraph checkpoints.

Public helpers accept **and return** plain dicts so the accumulator stays
JSON-serialisable without custom Pydantic serialisation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Model pricing (USD per 1 000 tokens).  Override with env vars later if the
# rates change — for now hard-coded as sensible defaults.
# ---------------------------------------------------------------------------

_MODEL_PRICING: dict[str, dict[str, float]] = {
    # input / output per 1K tokens
    "muse-spark-1.3-contributor": {"input": 0.003, "output": 0.015},
    "gpt-5.6-luna":              {"input": 0.005, "output": 0.020},
}

_DEFAULT_PRICING = {"input": 0.003, "output": 0.015}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def empty_metrics() -> dict[str, Any]:
    """Return a fresh, zero-valued metrics dict for a new application."""
    return {
        # Token tracking
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "llm_call_count": 0,

        # Cost
        "total_cost_usd": 0.0,

        # Tool / MCP call tracking
        "tool_call_count": 0,

        # Per-node elapsed seconds
        "node_timings": {},

        # Human intervention
        "human_intervention_count": 0,

        # Retry tracking
        "retry_count": 0,

        # Applied date (set when the submit button is confirmed)
        "applied_at": None,
    }


# ---------------------------------------------------------------------------
# Accumulators — each returns the updated dict for convenience
# ---------------------------------------------------------------------------

def record_llm_usage(
    metrics: dict[str, Any],
    model_name: str,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, Any]:
    """Add one LLM call's token counts and estimated cost."""
    metrics["total_input_tokens"] = metrics.get("total_input_tokens", 0) + input_tokens
    metrics["total_output_tokens"] = metrics.get("total_output_tokens", 0) + output_tokens
    metrics["total_tokens"] = metrics.get("total_tokens", 0) + input_tokens + output_tokens
    metrics["llm_call_count"] = metrics.get("llm_call_count", 0) + 1
    metrics["total_cost_usd"] = round(
        metrics.get("total_cost_usd", 0.0) + _compute_cost(model_name, input_tokens, output_tokens),
        6,
    )
    return metrics


def record_tool_call(metrics: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Increment the tool-call counter."""
    metrics["tool_call_count"] = metrics.get("tool_call_count", 0) + 1
    return metrics


def record_node_timing(
    metrics: dict[str, Any],
    node_name: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Store cumulative elapsed seconds for *node_name*."""
    timings = metrics.setdefault("node_timings", {})
    timings[node_name] = round(timings.get(node_name, 0.0) + elapsed_seconds, 3)
    return metrics


def record_human_intervention(metrics: dict[str, Any]) -> dict[str, Any]:
    """Increment the human-intervention counter."""
    metrics["human_intervention_count"] = metrics.get("human_intervention_count", 0) + 1
    return metrics


def record_retry(metrics: dict[str, Any]) -> dict[str, Any]:
    """Increment the retry counter."""
    metrics["retry_count"] = metrics.get("retry_count", 0) + 1
    return metrics


def stamp_applied_at(metrics: dict[str, Any]) -> dict[str, Any]:
    """Set the applied-at timestamp to *now* (UTC, ISO 8601)."""
    metrics["applied_at"] = datetime.now(timezone.utc).isoformat()
    return metrics


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _compute_cost(
    model_name: str, input_tokens: int, output_tokens: int,
) -> float:
    """Return estimated USD cost for a single LLM call."""
    pricing = _MODEL_PRICING.get(model_name, _DEFAULT_PRICING)
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000
