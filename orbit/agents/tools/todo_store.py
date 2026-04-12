"""Persisted todo list storage: single-agent sessions + shared orchestration scope.

- When ``session_key`` is ``orch:<orchId>``, todos live at
  ``<state>/orchestrations/<orchId>/shared_todos.json`` so every participant agent
  reads/writes the same file.
- Otherwise: ``<agent_workspace_dir>/.orbit/todos-<sessionId>.json`` (per conversation).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from ...config.paths import orchestration_state_dir

_ORCH_PREFIX = "orch:"
_SHARED_FILENAME = "shared_todos.json"


def _safe_session_slug(session_id: str) -> str:
    s = (session_id or "").strip() or "default"
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s)
    return s[:120] if len(s) > 120 else s


def resolve_todos_storage_path(
    *,
    session_key: Optional[str],
    session_id: Optional[str],
    agent_workspace_dir: str,
) -> Optional[str]:
    """Absolute path to the JSON store, or None if persistence is not available."""
    sk = (session_key or "").strip()
    if sk.startswith(_ORCH_PREFIX):
        oid = sk[len(_ORCH_PREFIX) :].strip()
        if not oid:
            return None
        root = orchestration_state_dir(oid)
        os.makedirs(root, exist_ok=True)
        return os.path.join(root, _SHARED_FILENAME)
    aw = (agent_workspace_dir or "").strip()
    if not aw:
        return None
    base = os.path.join(aw, ".orbit")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        return None
    slug = _safe_session_slug(str(session_id or ""))
    return os.path.join(base, f"todos-{slug}.json")


def read_todos_file(path: str) -> List[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict) and isinstance(data.get("todos"), list):
            return [x for x in data["todos"] if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    return []


def write_todos_file(path: str, todos: List[Dict[str, Any]]) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    payload = {"version": 1, "todos": todos}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def format_todos_snapshot_for_system(todos: List[Dict[str, Any]], *, shared_scope: bool) -> str:
    """Human-readable block appended to system / extra_system_prompt."""
    if not todos:
        return ""
    scope = (
        "此编排内所有 agent 共享同一清单（`session_key` 为 `orch:...`）。"
        if shared_scope
        else "此会话内有效；多轮对话会复用同一文件。"
    )
    lines = [
        "## Current todo list（当前待办）",
        "",
        scope,
        "",
        "以下由 `todo_write` 持久化；后续 agent 与下一轮应继续对齐这些项的状态。",
        "",
    ]
    for i, t in enumerate(todos, 1):
        st = str(t.get("status") or "?")
        c = str(t.get("content") or "").strip()
        af = str(t.get("active_form") or "").strip()
        lines.append(f"{i}. [{st}] {c}" + (f" — ({af})" if af else ""))
    return "\n".join(lines).strip()


def append_todos_snapshot_to_prompt(
    extra_system_prompt: Optional[str],
    *,
    session_key: Optional[str],
    session_id: Optional[str],
    agent_workspace_dir: str,
) -> Optional[str]:
    """If a non-empty todo file exists for this scope, append a snapshot to the system prompt."""
    path = resolve_todos_storage_path(
        session_key=session_key,
        session_id=session_id,
        agent_workspace_dir=agent_workspace_dir,
    )
    if not path:
        return extra_system_prompt
    todos = read_todos_file(path)
    if not todos:
        return extra_system_prompt
    shared = (session_key or "").strip().startswith(_ORCH_PREFIX)
    block = format_todos_snapshot_for_system(todos, shared_scope=shared)
    if not block:
        return extra_system_prompt
    prev = (extra_system_prompt or "").strip()
    if prev:
        return f"{prev}\n\n{block}".strip()
    return block
