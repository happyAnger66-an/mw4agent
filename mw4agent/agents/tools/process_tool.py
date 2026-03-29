"""Process management tool (OpenClaw-style `process`, simplified).

Supported actions:
- start: launch a background shell command
- status: inspect one process status
- stop: terminate one process
- list: list tracked processes
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .base import AgentTool, ToolResult
from .timeout_defaults import resolve_timeout_ms_param


def _ensure_under_root(resolved: str, root: str) -> None:
    root = os.path.normpath(os.path.abspath(root))
    resolved = os.path.normpath(os.path.abspath(resolved))
    if not resolved.startswith(root):
        raise PermissionError(f"process: cwd is outside workspace root: {root}")


@dataclass
class _ProcessRecord:
    process_id: str
    command: str
    cwd: str
    started_at_ms: int
    proc: asyncio.subprocess.Process


_PROCESS_REGISTRY: Dict[str, _ProcessRecord] = {}


class ProcessTool(AgentTool):
    """Manage long-running subprocesses."""

    def __init__(self) -> None:
        super().__init__(
            name="process",
            description="Manage background processes (start/status/stop/list). High-risk, owner-only.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "status", "stop", "list"],
                        "description": "Action to perform.",
                    },
                    "process_id": {
                        "type": "string",
                        "description": "Required for status/stop.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Required for start action.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory (relative to workspace or absolute path).",
                    },
                    "stop_timeout_ms": {
                        "type": "integer",
                        "description": "Optional timeout for graceful stop (default 3000).",
                    },
                },
                "required": ["action"],
            },
            owner_only=False,
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        action = str(params.get("action") or "").strip().lower()
        if action not in {"start", "status", "stop", "list"}:
            return ToolResult(success=False, result={}, error="process: action must be one of start/status/stop/list")

        workspace_dir = str((context or {}).get("workspace_dir") or os.getcwd())
        workspace_only = bool((context or {}).get("tools_fs_workspace_only") is True)

        if action == "start":
            return await self._start(params, workspace_dir, workspace_only)
        if action == "status":
            return self._status(params)
        if action == "stop":
            return await self._stop(params, context)
        return self._list()

    async def _start(self, params: Dict[str, Any], workspace_dir: str, workspace_only: bool) -> ToolResult:
        command = params.get("command")
        command = command.strip() if isinstance(command, str) else ""
        if not command:
            return ToolResult(success=False, result={}, error="process: command is required for start")

        cwd_raw = params.get("cwd")
        if isinstance(cwd_raw, str) and cwd_raw.strip():
            cwd = cwd_raw.strip()
            if not os.path.isabs(cwd):
                cwd = os.path.join(workspace_dir, cwd)
        else:
            cwd = workspace_dir
        cwd = os.path.normpath(os.path.abspath(cwd))
        if not os.path.isdir(cwd):
            return ToolResult(success=False, result={}, error=f"process: cwd does not exist: {cwd}")
        if workspace_only:
            try:
                _ensure_under_root(cwd, workspace_dir)
            except PermissionError as e:
                return ToolResult(success=False, result={}, error=str(e))

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            return ToolResult(success=False, result={}, error=f"process: failed to start: {e}")

        process_id = str(uuid.uuid4())
        rec = _ProcessRecord(
            process_id=process_id,
            command=command,
            cwd=cwd,
            started_at_ms=int(time.time() * 1000),
            proc=proc,
        )
        _PROCESS_REGISTRY[process_id] = rec
        return ToolResult(
            success=True,
            result={
                "process_id": process_id,
                "pid": proc.pid,
                "command": command,
                "cwd": cwd,
                "status": "running" if proc.returncode is None else "exited",
                "started_at_ms": rec.started_at_ms,
            },
        )

    def _status(self, params: Dict[str, Any]) -> ToolResult:
        process_id = str(params.get("process_id") or "").strip()
        if not process_id:
            return ToolResult(success=False, result={}, error="process: process_id is required for status")
        rec = _PROCESS_REGISTRY.get(process_id)
        if rec is None:
            return ToolResult(success=False, result={}, error=f"process: process_id not found: {process_id}")
        return ToolResult(
            success=True,
            result={
                "process_id": process_id,
                "pid": rec.proc.pid,
                "command": rec.command,
                "cwd": rec.cwd,
                "status": "running" if rec.proc.returncode is None else "exited",
                "exit_code": rec.proc.returncode,
                "started_at_ms": rec.started_at_ms,
            },
        )

    async def _stop(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> ToolResult:
        process_id = str(params.get("process_id") or "").strip()
        if not process_id:
            return ToolResult(success=False, result={}, error="process: process_id is required for stop")
        rec = _PROCESS_REGISTRY.get(process_id)
        if rec is None:
            return ToolResult(success=False, result={}, error=f"process: process_id not found: {process_id}")

        stop_timeout_ms = resolve_timeout_ms_param(
            params,
            context,
            param_key="stop_timeout_ms",
            default_ms=3000,
            min_ms=100,
            max_ms=20000,
        )

        if rec.proc.returncode is None:
            try:
                os.killpg(rec.proc.pid, signal.SIGTERM)
            except Exception:
                pass
            try:
                await asyncio.wait_for(rec.proc.wait(), timeout=stop_timeout_ms / 1000.0)
            except asyncio.TimeoutError:
                try:
                    os.killpg(rec.proc.pid, signal.SIGKILL)
                except Exception:
                    pass
                await rec.proc.wait()

        result = {
            "process_id": process_id,
            "pid": rec.proc.pid,
            "status": "exited",
            "exit_code": rec.proc.returncode,
        }
        _PROCESS_REGISTRY.pop(process_id, None)
        return ToolResult(success=True, result=result)

    def _list(self) -> ToolResult:
        items = []
        for process_id, rec in _PROCESS_REGISTRY.items():
            items.append(
                {
                    "process_id": process_id,
                    "pid": rec.proc.pid,
                    "command": rec.command,
                    "cwd": rec.cwd,
                    "status": "running" if rec.proc.returncode is None else "exited",
                    "exit_code": rec.proc.returncode,
                    "started_at_ms": rec.started_at_ms,
                }
            )
        items.sort(key=lambda x: int(x.get("started_at_ms") or 0), reverse=True)
        return ToolResult(success=True, result={"processes": items, "count": len(items)})

