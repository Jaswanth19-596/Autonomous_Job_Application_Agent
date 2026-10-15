OpenCode Go requests identify this application as `jobapplicationagent/0.1.0`
and send the LangGraph conversation's `configurable.thread_id` in
`x-opencode-session` on tool and structured-output calls.

`app.py` creates a new thread ID for each run. Set `JOB_AGENT_THREAD_ID` to the
existing ID when continuing a conversation. LangGraph Studio and other graph
callers must provide a stable `configurable.thread_id`; use a different ID for
each new conversation. IDs must contain printable ASCII characters without
spaces. Direct callers of the model factories in `nodes.py` can pass
`config={"configurable": {"thread_id": "your-conversation-id"}}` explicitly.

### Timed human intervention

When running `app.py`, application intervention prompts display a 1:00 countdown.
Replies submitted before the deadline are used normally. After 60 seconds without
an answer, questions return to the agent for a best reasonable inference from the
available profile and job context. These inferences are not saved as confirmed
user answers. Unresolved blockers (including CAPTCHA and login/security issues)
are recorded through the normal status update, and processing moves to the next
job. A repeated timed-out question also moves on instead of retrying indefinitely.
Initial conversation prompts remain untimed.

Other LangGraph clients must implement the countdown and automatic resume:
intervention payloads include `timeout_seconds: 60`; resume with
`{"human_input_timed_out": true}` on expiry. LangGraph Studio does not run the
command-line input handler.

### Job workbooks in Google Drive

The agent can use Excel workbooks stored in Google Drive as its source of truth.
It keeps the existing `.xlsx` format, sheets, and formatting. The three files are
`pending_jobs.xlsx`, `applied_jobs.xlsx`, and `invalid_jobs.xlsx` (rejected jobs).

After the current agent run finishes, stop it and run this once from the project:

```bash
uv run python -m workbook.drive_setup
```

Approve Google access in the browser. Setup reuses `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET` from `.env` (or the separate `GOOGLE_DRIVE_CLIENT_ID` and
`GOOGLE_DRIVE_CLIENT_SECRET` overrides). Enable the **Google Drive API** in the
Google Cloud project that owns this OAuth client if it is not already enabled.
The app requests only the `drive.file` scope for files it creates/accesses; its
separate Drive token is saved with owner-only permissions in `user/drive_token.json`.
Existing Gmail authorization is unchanged. A Codex/ChatGPT Drive connection does
not authorize this independent Python process.

Setup creates a private `JobApplicationAgent` folder, copies and verifies all
three workbooks, preserves local originals and a timestamped backup, and saves
file IDs in `user/drive_workbooks.json`. It enables Drive storage only after all
three copies succeed. Restart the agent for the next batch. Re-running setup
verifies the existing Drive files without overwriting them with local copies.
If interrupted, re-run setup to reuse matching uploaded files. For conflicting
partial uploads, `--new-folder` copies current local data to a separate folder;
it never deletes or replaces the conflicting files.

Each workbook read downloads the latest Drive copy into a temporary working file.
Each successful change is uploaded to the same Drive file before the operation
returns. This covers searches, pending queue updates, duplicate checks, applied
job rows/status/metrics, and rejected job records. Temporary files are removed
after use; `data/*.xlsx` originals are not refreshed and are no longer the source
of truth. Network, authorization, and upload failures surface as errors instead
of silently reading or writing stale local workbooks.

Writes are serialized between this checkout's processes. Version checks detect
Drive edits made during an operation, with conditional uploads when Google
returns an ETag. Use one agent writer across machines and avoid editing the Excel
files in Drive while a batch is running; these are whole-file updates, not a
distributed database or cross-workbook transaction. Failed rejected-job moves
can be retried: the rejected record uploads before its pending row is removed.

Check the connection with `uv run python -m workbook.drive_setup --status`.
Storage defaults to local until setup succeeds, then automatically uses Drive.
`JOB_AGENT_WORKBOOK_STORAGE=drive` requires Drive configuration and fails if it
is missing. `JOB_AGENT_WORKBOOK_STORAGE=local` explicitly uses the old originals;
it does **not** download current Drive data, so do not use it as an automatic
offline fallback. `JOB_AGENT_DRIVE_CONFIG` and `JOB_AGENT_DRIVE_TOKEN_PATH` can
override the configuration and token locations.

### Compare Playwright and Chrome MCP

The custom agent supports either browser MCP through `JOB_AGENT_BROWSER_MCP`.
To run with Playwright:

```bash
JOB_AGENT_BROWSER_MCP=playwright uv run python app.py
```

Use `JOB_AGENT_BROWSER_MCP=chrome` for Chrome DevTools MCP (the default).
In this mode, the agent receives only `trigger_extension_action`; all other
Chrome MCP tools are excluded by an explicit allowlist. Supply the installed
extension's ID to activate it. Navigation, page inspection, form filling, and
submission verification through Chrome MCP are unavailable in this mode.
This prevents agent calls to expensive diagnostic tools; it does not cap Chrome
memory or disable the browser's own background work. Playwright mode remains
separate and retains its browser tools.
You can also set this variable in `.env`. Restart the agent after switching.
Only the selected browser MCP is started. Both use the same dedicated Chrome
profile and debugging endpoint, preserving browser logins for comparison.
Playwright MCP is pinned to 0.0.83 and downloaded by npx on first use.
The existing JOB_AGENT_NODE_BINARY and JOB_AGENT_CHROME_* settings still apply.
This switches the automation server; it does not remove Chrome's memory usage.
After each job's final outcome is saved, the workflow stops the browser MCP
session and gracefully exits the dedicated Chrome instance. The next job
reopens Chrome when it first needs browser tools, using the same profile to
preserve saved logins. This applies to both browser backends and also closes
Chrome after the final job. Login/CAPTCHA pauses keep the browser open.
Only the configured dedicated debugging endpoint is closed; do not point it
at your everyday browser. Shutdown failures stop the batch before the next job.
