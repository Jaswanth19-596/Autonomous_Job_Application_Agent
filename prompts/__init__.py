from pathlib import Path
import os


PROMPTS_DIR = Path(__file__).parent


def load_prompt(filename: str, **values) -> str:
    """Load a prompt template and substitute its named placeholders."""
    template = (PROMPTS_DIR / filename).read_text(encoding="utf-8").rstrip("\n")
    template = template.format(**values) if values else template
    if os.getenv("JOB_AGENT_BROWSER_MCP", "chrome").strip().lower() == "playwright" and filename.endswith("system.txt"):
        template += """

Browser tool adapter (takes precedence over Chrome-specific tool names above):
Use only the available Playwright MCP tools and their actual schemas.
For list_pages/select_page/close_page/new_page use browser_tabs with the
corresponding action. Tab indexes can change after closing a tab: list tabs
again before selecting or closing another. For navigate_page use browser_navigate;
for take_snapshot use browser_snapshot; for evaluate_script use browser_evaluate.
Tools act on the selected tab; do not pass Chrome pageId arguments.
For handle_dialog use browser_handle_dialog with accept=true or false, rather
than Chrome's action parameter. Use browser_file_upload for file uploads.
Use fresh snapshot element refs with browser_click/browser_fill_form/browser_type.
Preserve all existing job verification, consent, and tab retention rules.
"""
    if os.getenv("JOB_AGENT_BROWSER_MCP", "chrome").strip().lower() == "chrome" and filename.endswith("system.txt"):
        template += """

Chrome tool restriction (takes precedence over browser instructions above):
The only available Chrome MCP tool is trigger_extension_action. It activates an
installed extension using its ID. Do not call other Chrome tools or claim to
navigate, inspect pages, fill forms, or verify submission using this tool alone.
Use an extension ID supplied by the user or trusted configuration; do not guess.
If a required browser action or verification is unavailable, explain the limitation
and request human assistance rather than looping or reporting success.
"""
    return template
