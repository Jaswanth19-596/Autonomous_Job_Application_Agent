"""Human-readable, real-time terminal progress for workflow execution."""

import json
import logging
from pathlib import Path
import re
from functools import wraps
from inspect import iscoroutinefunction
from collections import deque
from collections.abc import Awaitable, Mapping
from datetime import datetime
from typing import Any, Callable, TypeVar
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

try:
    from langchain_core.messages import BaseMessage
except ImportError:
    BaseMessage = None


T = TypeVar("T")
_MAX_TOOL_OUTPUT_CHARS = 2_000
_MAX_VALUE_CHARS = 240
console = Console(highlight=False)

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
NODE_STATE_LOG_FILE_PATH = LOGS_DIR / "node_states.log"

LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Dedicated logger for workflow node states
_node_state_file_logger = logging.getLogger("node_state_file_logger")
_node_state_file_logger.setLevel(logging.INFO)
_node_state_file_logger.propagate = False

if not _node_state_file_logger.handlers:
    _state_file_handler = logging.FileHandler(NODE_STATE_LOG_FILE_PATH, mode="a", encoding="utf-8")
    _state_file_handler.setLevel(logging.INFO)
    _state_file_formatter = logging.Formatter("%(message)s")
    _state_file_handler.setFormatter(_state_file_formatter)
    _node_state_file_logger.addHandler(_state_file_handler)

_workflow_logger = logging.getLogger("workflow_state")


def _print_progress(message: str) -> None:
    """Print immediately, including when stdout is redirected or buffered."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    console.print(Text(f"[{timestamp}] {message}", style="dim"))


def log_node_stage(node_name: str, stage: str) -> None:
    """Print intra-node stage progress with timestamp."""
    _print_progress(f"[{node_name}] {stage}")


def log_workflow_event(event: str, **details: Any) -> None:
    """Print a compact, redacted workflow decision or state transition."""
    detail_text = ""
    if details:
        safe_details = _shorten_values(_redact_sensitive_values(details))
        detail_text = " " + json.dumps(safe_details, ensure_ascii=False, default=str)
    _print_progress(f"↳ {event}{detail_text}")


def _serialize_for_state_log(obj: Any) -> Any:
    """Safely convert state objects (including LangChain messages and Pydantic models) to JSON-serializable structures."""
    if obj is None or isinstance(obj, (int, float, bool, str)):
        return obj
    if BaseMessage is not None and isinstance(obj, BaseMessage):
        msg_dict: dict[str, Any] = {
            "type": obj.__class__.__name__,
            "content": obj.content,
        }
        if getattr(obj, "name", None):
            msg_dict["name"] = obj.name
        if getattr(obj, "tool_calls", None):
            msg_dict["tool_calls"] = obj.tool_calls
        if getattr(obj, "tool_call_id", None):
            msg_dict["tool_call_id"] = obj.tool_call_id
        return msg_dict
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        try:
            return _serialize_for_state_log(obj.model_dump())
        except Exception:
            pass
    if hasattr(obj, "dict") and callable(obj.dict):
        try:
            return _serialize_for_state_log(obj.dict())
        except Exception:
            pass
    if isinstance(obj, Mapping):
        return {str(k): _serialize_for_state_log(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, deque)):
        return [_serialize_for_state_log(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return _serialize_for_state_log(vars(obj))
    return str(obj)


def format_node_state(state: Any) -> dict[str, Any]:
    """Convert and sanitize a workflow state dict for logging."""
    if state is None:
        return {}
    serialized = _serialize_for_state_log(state)
    if isinstance(serialized, dict):
        return _redact_sensitive_values(serialized)
    return {"state": _redact_sensitive_values(serialized)}


def _format_state_summary(state: Any) -> str:
    """Generate a compact one-line string summarizing the workflow state for terminal progress."""
    if not isinstance(state, Mapping):
        return ""
    parts: list[str] = []
    if state.get("user_node_decision"):
        parts.append(f"decision={state['user_node_decision']}")
    if state.get("current_job"):
        job = state["current_job"]
        if isinstance(job, dict):
            job_title = job.get("job_title") or job.get("title") or job.get("job_url") or "active_job"
        else:
            job_title = str(job)
        parts.append(f"job='{_shorten_values(job_title)}'")
    elif state.get("job_url"):
        parts.append(f"url='{_shorten_values(state['job_url'])}'")
    if state.get("application_queue"):
        parts.append(f"queue_len={len(state['application_queue'])}")
    if state.get("validation_phase") and state.get("validation_phase") != "ELIGIBLE":
        parts.append(f"val_phase={state['validation_phase']}")
    if state.get("resolution_phase") and state.get("resolution_phase") != "FAILED":
        parts.append(f"res_phase={state['resolution_phase']}")
    if state.get("login_phase") and state.get("login_phase") != "FAILED":
        parts.append(f"login_phase={state['login_phase']}")
    if state.get("job_application_phase") and state.get("job_application_phase") != "NOT_APPLIED":
        parts.append(f"app_phase={state['job_application_phase']}")
    if state.get("verification_phase") and state.get("verification_phase") != "NOT_APPLIED":
        parts.append(f"verif_phase={state['verification_phase']}")
    if state.get("requires_human"):
        parts.append("requires_human=True")
    return f" ({', '.join(parts)})" if parts else ""


def log_node_state(node_name: str, state: Any) -> None:
    """Log the state snapshot upon entering a workflow node to persistent log file and python logger."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    sanitized_state = format_node_state(state)

    summary_lines: list[str] = []
    if isinstance(sanitized_state, dict):
        if "user_node_decision" in sanitized_state:
            summary_lines.append(f"  • Decision: {sanitized_state['user_node_decision']}")
        if "current_job" in sanitized_state and sanitized_state["current_job"]:
            job_val = sanitized_state["current_job"]
            if isinstance(job_val, dict):
                job_desc = job_val.get("job_title") or job_val.get("title") or job_val.get("job_url") or "Job"
            else:
                job_desc = str(job_val)
            summary_lines.append(f"  • Current Job: {job_desc}")
        if "job_url" in sanitized_state and sanitized_state["job_url"]:
            summary_lines.append(f"  • Job URL: {sanitized_state['job_url']}")
        if "actual_job_url" in sanitized_state and sanitized_state["actual_job_url"]:
            summary_lines.append(f"  • Resolved URL: {sanitized_state['actual_job_url']}")
        if "application_queue" in sanitized_state and isinstance(sanitized_state["application_queue"], list):
            summary_lines.append(f"  • Application Queue: {len(sanitized_state['application_queue'])} job(s)")

        phases: list[str] = []
        for phase_key in ("validation_phase", "resolution_phase", "login_phase", "job_application_phase", "verification_phase"):
            if phase_key in sanitized_state:
                phases.append(f"{phase_key}={sanitized_state[phase_key]}")
        if phases:
            summary_lines.append(f"  • Phases: {' | '.join(phases)}")

        flags: list[str] = []
        for flag_key in ("requires_human", "is_applied", "is_valid", "authenticated_session"):
            if flag_key in sanitized_state:
                flags.append(f"{flag_key}={sanitized_state[flag_key]}")
        if flags:
            summary_lines.append(f"  • Flags: {' | '.join(flags)}")

    summary_section = "\n".join(summary_lines) if summary_lines else "  • (No key workflow flags set)"

    try:
        formatted_json = json.dumps(sanitized_state, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        formatted_json = f"<Failed to format state as JSON: {exc}>\n{str(sanitized_state)}"

    log_entry = (
        f"\n{'='*80}\n"
        f"[{timestamp}] ENTERING NODE: {node_name}\n"
        f"{'-'*80}\n"
        f"WORKFLOW SUMMARY:\n"
        f"{summary_section}\n"
        f"{'-'*80}\n"
        f"STATE SNAPSHOT:\n"
        f"{formatted_json}\n"
        f"{'='*80}\n"
    )

    _node_state_file_logger.info(log_entry)
    _workflow_logger.info(f"Entered node {node_name}")


def trace_node(function: Callable[..., T]) -> Callable[..., T]:
    """Print concise lifecycle lines for every graph node and log state at entry."""
    node_name = function.__name__

    if iscoroutinefunction(function):
        @wraps(function)
        async def async_wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> T:
            log_node_state(node_name, state)
            state_summary = _format_state_summary(state)
            _print_progress(f"→ {node_name}{state_summary}")
            try:
                result = await function(state, *args, **kwargs)
            except Exception as exc:
                _print_progress(f"{node_name} — failed: {type(exc).__name__}: {exc}")
                raise
            _print_progress(f"← {node_name}")
            return result

        return async_wrapper

    @wraps(function)
    def sync_wrapper(state: dict[str, Any], *args: Any, **kwargs: Any) -> T:
        log_node_state(node_name, state)
        state_summary = _format_state_summary(state)
        _print_progress(f"→ {node_name}{state_summary}")
        try:
            result = function(state, *args, **kwargs)
        except Exception as exc:
            _print_progress(f"{node_name} — failed: {type(exc).__name__}: {exc}")
            raise
        _print_progress(f"← {node_name}")
        return result

    return sync_wrapper


def _redact_sensitive_values(value: Any) -> Any:
    sensitive_keys = {"password", "token", "secret", "authorization", "api_key"}

    if isinstance(value, dict):
        return {
            key: (
                "***REDACTED***"
                if key.lower() in sensitive_keys
                else _redact_sensitive_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def _shorten_values(value: Any) -> Any:
    """Keep the terminal log readable when a tool receives a long URL or script."""
    if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
        return f"{value[:_MAX_VALUE_CHARS - 3]}..."
    if isinstance(value, dict):
        return {key: _shorten_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shorten_values(item) for item in value]
    return value


def _extract_text_output(tool_result: Any) -> str:
    """Unwrap MCP text blocks instead of printing their Python representation."""
    if isinstance(tool_result, dict):
        if isinstance(tool_result.get("text"), str):
            return tool_result["text"]
        return json.dumps(tool_result, indent=2, ensure_ascii=False, default=str)

    if isinstance(tool_result, (list, tuple)):
        parts = [_extract_text_output(item) for item in tool_result]
        return "\n".join(part for part in parts if part)

    content = getattr(tool_result, "content", None)
    if content is not None:
        return _extract_text_output(content)

    return str(tool_result)


def log_tool_calls(node_name: str, tool_calls: list[dict[str, Any]]) -> None:
    """Show requested tools while keeping credentials out of the terminal."""
    for index, tool_call in enumerate(tool_calls, start=1):
        arguments = json.dumps(
            _shorten_values(_redact_sensitive_values(tool_call["args"])),
            indent=2,
            default=str,
            ensure_ascii=False,
        )
        timestamp = datetime.now().strftime("%H:%M:%S")
        console.print()
        console.print(
            Panel(
                Syntax(arguments, "json", theme="monokai", word_wrap=True),
                title=(
                    f"[dim][{timestamp}][/dim] "
                    f"[bold cyan]{node_name}[/bold cyan] "
                    f"[bold white]› {tool_call['name']}[/bold white]"
                ),
                subtitle="[dim]tool call[/dim]",
                border_style="cyan",
                padding=(0, 1),
                expand=False,
            )
        )


def _redact_sensitive_text(value: str) -> str:
    """Redact common credential fields when an external tool returns plain text."""
    return re.sub(
        r'(?i)(password|token|secret|authorization|api_key)(\s*[=:]\s*["\']?)([^\s,"\']+)',
        r'\1\2***REDACTED***',
        value,
    )


def log_tool_result(node_name: str, tool_name: str, tool_result: Any) -> None:
    """Show a completed tool call and a safely bounded version of its output."""
    output = _redact_sensitive_text(_extract_text_output(tool_result)).strip()
    if len(output) > _MAX_TOOL_OUTPUT_CHARS:
        output = f"{output[:_MAX_TOOL_OUTPUT_CHARS]}... [output truncated]"
    if not output:
        output = "(no text output)"
    timestamp = datetime.now().strftime("%H:%M:%S")
    console.print(
        Panel(
            Text(output),
            title=(
                f"[dim][{timestamp}][/dim] "
                f"[bold green]{node_name}[/bold green] "
                f"[bold white]✓ {tool_name}[/bold white]"
            ),
            subtitle="[dim]tool result[/dim]",
            border_style="green",
            padding=(0, 1),
            expand=False,
        )
    )


async def await_with_progress(
    node_name: str,
    activity: str,
    awaitable: Awaitable[T],
    *,
    show_progress: bool = True,
) -> T:
    """Await work in the current task while showing real-time progress and duration."""
    start_time = datetime.now()
    if show_progress:
        _print_progress(f"[{node_name}] Starting: {activity}")
        try:
            with console.status(
                f"[bold yellow]◌ {node_name}[/bold yellow] [dim]— {activity}[/dim]",
                spinner="dots",
                spinner_style="yellow",
            ):
                result = await awaitable
            elapsed = (datetime.now() - start_time).total_seconds()
            _print_progress(f"[{node_name}] Finished: {activity} ({elapsed:.1f}s)")
            return result
        except Exception as exc:
            elapsed = (datetime.now() - start_time).total_seconds()
            _print_progress(f"[{node_name}] Failed: {activity} ({elapsed:.1f}s) — {type(exc).__name__}: {exc}")
            raise
    try:
        result = await awaitable
        return result
    except Exception as exc:
        elapsed = (datetime.now() - start_time).total_seconds()
        _print_progress(f"[{node_name}] Failed: {activity} ({elapsed:.1f}s) — {type(exc).__name__}: {exc}")
        raise
