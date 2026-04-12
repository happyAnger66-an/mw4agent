"""Session todo list tool (Claude Code TodoWrite-style, full-list updates + disk)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...log import get_logger
from .base import AgentTool, ToolResult
from .todo_store import read_todos_file, resolve_todos_storage_path, write_todos_file
from .todo_write_prompt import TODO_WRITE_TOOL_USAGE_FOR_DESCRIPTION

logger = get_logger(__name__)

_ORBIT_TODOS_KEY = "_orbit_todos"

_VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})

_MAX_TODOS = 100


def _pick_str(d: Dict[str, Any], *keys: str) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _normalize_todo_item(raw: Any, index: int) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"todos[{index}] must be an object")
    content = _pick_str(raw, "content", "text", "title")
    if not content:
        raise ValueError(f"todos[{index}].content is required (non-empty string)")
    status = _pick_str(raw, "status", "state")
    if not status:
        raise ValueError(f"todos[{index}].status is required")
    status_l = status.lower().replace("-", "_")
    if status_l == "in progress":
        status_l = "in_progress"
    if status_l not in _VALID_STATUSES:
        raise ValueError(
            f"todos[{index}].status must be one of {sorted(_VALID_STATUSES)}, got {status!r}"
        )
    active = _pick_str(raw, "active_form", "activeForm")
    if not active:
        raise ValueError(
            f"todos[{index}].active_form is required (present continuous, e.g. 'Running tests')"
        )
    return {"content": content, "status": status_l, "active_form": active}


class TodoWriteTool(AgentTool):
    """Replace the persisted todo list for the session / orchestration scope."""

    def __init__(self) -> None:
        super().__init__(
            name="todo_write",
            description=(
                "Update the session task checklist: pass the full `todos` array each time. "
                "Each item needs content, status (pending|in_progress|completed), and "
                "active_form (present continuous, e.g. 'Reading config'). "
                "Use for multi-step work; skip for single trivial steps."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Complete replacement list of todo items for this session.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "Short description of the task.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Task state.",
                                },
                                "active_form": {
                                    "type": "string",
                                    "description": "Present continuous label while in_progress (e.g. 'Running tests').",
                                },
                            },
                            "required": ["content", "status", "active_form"],
                        },
                    },
                },
                "required": ["todos"],
            },
            owner_only=False,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Align with Claude Code: long guidance lives on the tool description, not system."""
        d = super().to_dict()
        base = (d.get("description") or "").strip()
        extra = TODO_WRITE_TOOL_USAGE_FOR_DESCRIPTION.strip()
        d["description"] = f"{base}\n\n{extra}".strip() if extra else base
        return d

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        ctx = context if isinstance(context, dict) else {}
        raw_list = params.get("todos")
        if raw_list is None:
            return ToolResult(
                success=False,
                result={},
                error="todos is required (array of items)",
            )
        if not isinstance(raw_list, list):
            return ToolResult(
                success=False,
                result={},
                error="todos must be an array",
            )
        if len(raw_list) > _MAX_TODOS:
            return ToolResult(
                success=False,
                result={},
                error=f"at most {_MAX_TODOS} todos allowed",
            )

        path = resolve_todos_storage_path(
            session_key=ctx.get("session_key") if isinstance(ctx.get("session_key"), str) else None,
            session_id=ctx.get("session_id") if isinstance(ctx.get("session_id"), str) else None,
            agent_workspace_dir=str(ctx.get("agent_workspace_dir") or ""),
        )

        if path:
            old_list = read_todos_file(path)
        else:
            old = ctx.get(_ORBIT_TODOS_KEY)
            old_list = [dict(x) for x in old if isinstance(x, dict)] if isinstance(old, list) else []

        try:
            normalized = [_normalize_todo_item(x, i) for i, x in enumerate(raw_list)]
        except ValueError as e:
            return ToolResult(success=False, result={}, error=str(e))

        all_done = bool(normalized) and all(
            str(x.get("status")) == "completed" for x in normalized
        )
        new_stored: List[Dict[str, Any]] = [] if all_done else normalized

        if path:
            try:
                write_todos_file(path, new_stored)
            except OSError as e:
                return ToolResult(
                    success=False,
                    result={},
                    error=f"failed to write todo file: {e}",
                )
        ctx[_ORBIT_TODOS_KEY] = new_stored

        payload = {
            "storagePath": path,
            "oldTodos": old_list,
            "newTodos": normalized,
            "storedTodos": new_stored,
            "clearedBecauseAllCompleted": all_done,
        }
        logger.debug(
            "todo_write tool_call_id=%s path=%s count=%s cleared=%s",
            tool_call_id,
            path,
            len(normalized),
            all_done,
        )
        return ToolResult(success=True, result=payload, metadata=None)
