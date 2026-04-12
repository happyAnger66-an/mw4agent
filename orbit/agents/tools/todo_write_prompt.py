"""Long-form usage text merged into the ``todo_write`` tool ``description`` (Claude Code style).

The agent runner separately appends a **snapshot** of persisted todos to
``extra_system_prompt`` when a todo file exists (see ``todo_store.py``), so every
agent turn — including orchestration hand-offs — sees the current checklist.
"""

from __future__ import annotations

# Appended to the short base description in ``TodoWriteTool.to_dict()``.
TODO_WRITE_TOOL_USAGE_FOR_DESCRIPTION = """
### When to use todo_write
- Multi-step or non-trivial work (typically 3+ distinct steps).
- User asked for a checklist, or gave several tasks at once.
- After new instructions: capture requirements as todos; set one item to `in_progress` before starting it (prefer only one in_progress); mark `completed` when done and add follow-ups you discover.

### When not to use
- Single trivial step or purely conversational Q&A.

### Semantics
- Each call sends the **full** `todos` array (replaces the stored list).
- Fields per item: `content` (string), `status` (`pending` | `in_progress` | `completed`), `active_form` (present continuous, e.g. "Running tests"). Alias `activeForm` is accepted.
- If **every** item is `completed`, the persisted list is **cleared** (same as Claude Code); the tool result still returns the final `newTodos` for the transcript.

### Scope / sharing
- If `session_key` is `orch:<orchestrationId>`, todos are stored under that orchestration and **shared by all agents** in the run.
- Otherwise todos are stored per agent workspace and session id (one file per conversation).
""".strip()
