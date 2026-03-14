"""Read file tool - OpenClaw-style, workspace-scoped.

Reference: openclaw src/agents/pi-tools.read.ts, createReadTool.
- path: relative to workspace_dir or absolute (must be under workspace when provided).
- offset: optional 1-based line offset for paging.
- limit: optional max lines to return (default ~50KB of text if not set).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import AgentTool, ToolResult


def _resolve_path(path: str, workspace_dir: str) -> str:
    """Resolve path against workspace. Supports path or file_path."""
    path = (path or "").strip()
    if not path:
        raise ValueError("read: path is required")
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(workspace_dir, path))


def _ensure_under_root(resolved: str, root: str) -> None:
    root = os.path.normpath(os.path.abspath(root))
    resolved = os.path.normpath(os.path.abspath(resolved))
    if not resolved.startswith(root):
        raise PermissionError(f"read: path is outside workspace root: {root}")


class ReadTool(AgentTool):
    """Read file contents. Path is relative to workspace_dir from context."""

    def __init__(self) -> None:
        super().__init__(
            name="read",
            description="Read the contents of a file. Use path relative to workspace or absolute path inside workspace. Optionally use offset (1-based line) and limit (max lines) for large files.",
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
                    "offset": {
                        "type": "integer",
                        "description": "Optional 1-based line number to start reading (for paging).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional max number of lines to return (default: reasonable page size).",
                    },
                },
                "required": ["path"],
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
        path = params.get("path") or params.get("file_path")
        path = path.strip() if isinstance(path, str) else None
        if not path:
            return ToolResult(success=False, result={}, error="read: path is required")

        try:
            resolved = _resolve_path(path, workspace_dir)
            _ensure_under_root(resolved, workspace_dir)
        except (ValueError, PermissionError) as e:
            return ToolResult(success=False, result={}, error=str(e))

        offset = params.get("offset")
        limit = params.get("limit")
        if offset is not None:
            try:
                offset = int(offset)
            except (TypeError, ValueError):
                offset = None
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                limit = None
        if limit is not None and limit <= 0:
            limit = None
        if limit is None:
            limit = 2000  # default page

        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return ToolResult(
                success=False,
                result={"path": path},
                error=f"read: file not found: {path}",
            )
        except IsADirectoryError:
            return ToolResult(
                success=False,
                result={"path": path},
                error=f"read: path is a directory: {path}",
            )
        except OSError as e:
            return ToolResult(success=False, result={"path": path}, error=f"read: {e}")

        start = 0 if offset is None else max(0, offset - 1)
        end = min(len(lines), start + limit) if limit else len(lines)
        selected = lines[start:end]
        text = "".join(selected)

        details = {
            "path": path,
            "resolved": resolved,
            "totalLines": len(lines),
        }
        if offset is not None or limit is not None:
            details["offset"] = start + 1
            details["linesReturned"] = len(selected)
        if end < len(lines):
            details["truncated"] = True
            details["nextOffset"] = end + 1

        return ToolResult(
            success=True,
            result={"content": text, "details": details},
            metadata={"path": path},
        )
