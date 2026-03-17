"""Write file tool - OpenClaw-style, workspace-scoped.

Reference: openclaw src/agents/pi-tools.read.ts createWriteTool, CLAUDE_PARAM_GROUPS.write.
- path (or file_path): relative to workspace_dir or absolute within workspace.
- content: string to write.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import AgentTool, ToolResult


def _resolve_path(path: str, workspace_dir: str) -> str:
    path = (path or "").strip()
    if not path:
        raise ValueError("write: path is required")
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(workspace_dir, path))


def _ensure_under_root(resolved: str, root: str) -> None:
    root = os.path.normpath(os.path.abspath(root))
    resolved = os.path.normpath(os.path.abspath(resolved))
    if not resolved.startswith(root):
        raise PermissionError(f"write: path is outside workspace root: {root}")


class WriteTool(AgentTool):
    """Write content to a file. Path is relative to workspace_dir from context."""

    def __init__(self) -> None:
        super().__init__(
            name="write",
            description=(
                "Write content to a file. Path is relative to workspace (e.g. MEMORY.md, memory/notes.md). "
                "Creates parent directories if needed. Writing to MEMORY.md or memory/*.md persists to long-term memory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (relative to workspace or absolute within workspace).",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Alias for path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
            owner_only=False,
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        workspace_dir = (context or {}).get("workspace_dir") or os.getcwd()
        workspace_only = bool((context or {}).get("tools_fs_workspace_only") is True)
        path = params.get("path") or params.get("file_path")
        path = path.strip() if isinstance(path, str) else None
        if not path:
            return ToolResult(success=False, result={}, error="write: path is required")

        content = params.get("content")
        if content is None:
            return ToolResult(success=False, result={}, error="write: content is required")
        if not isinstance(content, str):
            content = str(content)

        try:
            resolved = _resolve_path(path, workspace_dir)
            if workspace_only:
                _ensure_under_root(resolved, workspace_dir)
        except (ValueError, PermissionError) as e:
            return ToolResult(success=False, result={}, error=str(e))

        try:
            os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            return ToolResult(success=False, result={"path": path}, error=f"write: {e}")

        return ToolResult(
            success=True,
            result={"path": path, "resolved": resolved, "wrote": len(content)},
            metadata={"path": path},
        )
