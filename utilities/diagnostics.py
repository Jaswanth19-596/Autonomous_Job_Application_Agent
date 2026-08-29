"""Human-readable, real-time terminal progress for workflow execution."""

import json
import re
from collections.abc import Awaitable
from datetime import datetime
from typing import Any, TypeVar
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


T = TypeVar("T")
_MAX_TOOL_OUTPUT_CHARS = 2_000
_MAX_VALUE_CHARS = 240
console = Console(highlight=False)


def _print_progress(message: str) -> None:
    """Print immediately, including when stdout is redirected or buffered."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    console.print(Text(f"[{timestamp}] {message}", style="dim"))


def log_node_stage(node_name: str, stage: str) -> None:
    """Keep stage events available to callers without printing them by default."""


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
    """Await work in the current task while optionally showing a spinner."""
    if show_progress:
        with console.status(
            f"[bold yellow]◌ {node_name}[/bold yellow] [dim]— {activity}[/dim]",
            spinner="dots",
            spinner_style="yellow",
        ):
            return await awaitable
    return await awaitable
