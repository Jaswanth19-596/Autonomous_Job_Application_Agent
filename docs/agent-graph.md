# Job Application Agent Graph

Use the two Mermaid sources instead of one dense graph:

- `agent-graph.mmd` is the clean, editable workflow overview.
- `agent-tool-resume.mmd` is the companion diagram for `tool_node`, login, and
  human-review return paths. Import it separately and place it beside the
  overview in Excalidraw.

## Adding a node

For example, to insert `tailor_resume` after validation and before URL
resolution, replace:

```mermaid
validate -->|ELIGIBLE| resolve
```

with:

```mermaid
validate -->|ELIGIBLE| tailor[tailor_resume]
tailor --> resolve
```

Then register the node and its edges in `app.py`; the diagram itself does not
change the workflow.
