"""Memory tools for agent: memory_search, memory_get (OpenClaw-style).

Semantically search MEMORY.md + memory/*.md and read slices by path.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ... import memory
from ...memory.backend import get_memory_backend, SearchOptions
from ...log import get_logger
from .base import AgentTool, ToolResult

logger = get_logger(__name__)


def _read_string_param(params: Dict[str, Any], key: str, required: bool = False) -> Optional[str]:
    v = params.get(key)
    if v is None and required:
        return None
    if isinstance(v, str):
        return v.strip() or None
    return str(v).strip() if v is not None else None


def _read_number_param(
    params: Dict[str, Any],
    key: str,
    *,
    integer: bool = False,
) -> Optional[float]:
    v = params.get(key)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(int(v) if integer else v)
    try:
        x = float(v)
        return float(int(x) if integer else x)
    except (TypeError, ValueError):
        return None


def _json_result(data: Dict[str, Any]) -> ToolResult:
    return ToolResult(success=True, result=data)


class MemorySearchTool(AgentTool):
    """Semantic search over MEMORY.md + memory/*.md (Phase 1: keyword)."""

    def __init__(self) -> None:
        super().__init__(
            name="memory_search",
            description=(
                "Mandatory recall step: search MEMORY.md and memory/*.md (and optional session "
                "transcripts) before answering questions about prior work, decisions, dates, people, "
                "preferences, or todos; returns top snippets with path and line range. "
                "If disabled=true, memory retrieval is unavailable."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keywords or natural language).",
                    },
                    "maxResults": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                    },
                    "minScore": {
                        "type": "number",
                        "description": "Minimum score threshold (0-1).",
                    },
                },
                "required": ["query"],
            },
            owner_only=False,
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        query = _read_string_param(params, "query", required=True)
        if not query:
            return _json_result({
                "results": [],
                "disabled": True,
                "error": "query is required",
            })
        workspace_dir = (context or {}).get("workspace_dir") or ""
        if not workspace_dir:
            return _json_result({
                "results": [],
                "disabled": True,
                "error": "workspace_dir not set",
            })
        max_results = _read_number_param(params, "maxResults", integer=True)
        min_score = _read_number_param(params, "minScore")
        max_results = int(max_results) if max_results is not None else 10
        min_score = float(min_score) if min_score is not None else 0.0
        try:
            files = memory.list_memory_files(workspace_dir)
            logger.info(
                "memory_search workspace_dir=%s files=%s query=%r",
                workspace_dir,
                files,
                query[:80] if query else "",
            )
            ctx = context or {}
            session_id = ctx.get("session_id")
            agent_id = ctx.get("agent_id")
            options = SearchOptions(
                max_results=max_results,
                min_score=min_score,
                session_key=ctx.get("session_key"),
                session_id=str(session_id).strip() if isinstance(session_id, str) and session_id.strip() else None,
                agent_id=str(agent_id).strip() if isinstance(agent_id, str) and agent_id.strip() else None,
            )
            backend = get_memory_backend()
            raw = backend.search(
                query,
                workspace_dir,
                options=options,
            )
            results = []
            for r in raw:
                item: Dict[str, Any] = {
                    "path": r.path,
                    "startLine": r.start_line,
                    "endLine": r.end_line,
                    "score": r.score,
                    "snippet": r.snippet,
                    "source": r.source,
                }
                if getattr(r, "session_id", None):
                    item["sessionId"] = r.session_id
                if getattr(r, "created_at", None) is not None:
                    item["createdAt"] = r.created_at
                if getattr(r, "updated_at", None) is not None:
                    item["updatedAt"] = r.updated_at
                results.append(item)
            return _json_result({
                "results": results,
                "provider": "file",
                "mode": "keyword",
            })
        except Exception as e:
            return _json_result({
                "results": [],
                "disabled": True,
                "unavailable": True,
                "error": str(e),
                "warning": "Memory search failed.",
                "action": "Check workspace and MEMORY.md / memory/*.md.",
            })


class MemoryGetTool(AgentTool):
    """Read a slice of MEMORY.md or memory/*.md by path and optional from/lines."""

    def __init__(self) -> None:
        super().__init__(
            name="memory_get",
            description=(
                "Safe snippet read from MEMORY.md or memory/*.md with optional from/lines; "
                "use after memory_search to pull only the needed lines and keep context small."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path (e.g. MEMORY.md, memory/notes.md).",
                    },
                    "from": {
                        "type": "integer",
                        "description": "1-based line number to start reading.",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to return.",
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
        path = _read_string_param(params, "path", required=True)
        if not path:
            return _json_result({"path": "", "text": "", "disabled": True, "error": "path is required"})
        workspace_dir = (context or {}).get("workspace_dir") or ""
        if not workspace_dir:
            return _json_result({"path": path, "text": "", "disabled": True, "error": "workspace_dir not set"})
        from_line = _read_number_param(params, "from", integer=True)
        lines = _read_number_param(params, "lines", integer=True)
        from_line = int(from_line) if from_line is not None else None
        lines = int(lines) if lines is not None else None
        try:
            ctx = context or {}
            session_id = str(ctx.get("session_id") or "").strip() or None
            agent_id = str(ctx.get("agent_id") or "").strip() or None
            backend = get_memory_backend()
            r = backend.read_file(
                workspace_dir,
                path,
                from_line=from_line,
                lines=lines,
                session_id=session_id,
                agent_id=agent_id,
            )
            return _json_result({
                "path": r.path,
                "text": r.text,
                "missing": r.missing,
            })
        except Exception as e:
            return _json_result({
                "path": path,
                "text": "",
                "disabled": True,
                "error": str(e),
            })


class MemoryWriteTool(AgentTool):
    """Write or append to MEMORY.md or memory/*.md so content can be recalled later via memory_search."""

    def __init__(self) -> None:
        super().__init__(
            name="memory_write",
            description=(
                "Persist information to long-term memory so it can be recalled later. "
                "Write or append to MEMORY.md (main memory) or memory/<topic>.md. "
                "Use after summarizing a decision, preference, or fact the user wants remembered."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Memory file path: MEMORY.md for main memory, or memory/notes.md, memory/todos.md, etc.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write (Markdown). Will overwrite the file unless append=true.",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "If true, append to existing file instead of overwriting. Default false.",
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
        path = _read_string_param(params, "path", required=True)
        content = params.get("content")
        if content is None:
            return ToolResult(success=False, result={}, error="memory_write: content is required")
        if not isinstance(content, str):
            content = str(content)
        append = bool(params.get("append") is True)
        workspace_dir = (context or {}).get("workspace_dir") or ""
        if not workspace_dir:
            return ToolResult(
                success=False,
                result={},
                error="memory_write: workspace_dir not set",
            )
        try:
            ok, err = memory.write_memory_file(
                workspace_dir,
                path,
                content,
                append=append,
            )
            if not ok:
                return ToolResult(success=False, result={"path": path}, error=err)
            return ToolResult(
                success=True,
                result={"path": path, "append": append, "wrote": len(content)},
                metadata={"path": path},
            )
        except Exception as e:
            return ToolResult(success=False, result={"path": path}, error=str(e))
