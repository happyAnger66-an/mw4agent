"""Command execution tool (OpenClaw-style `exec`, simplified).

Security posture in MW4Agent:
- owner_only=True by default (high-risk tool).
- Optional cwd, default to workspace_dir from context.
- Respect tools_fs_workspace_only by requiring cwd under workspace root.
- Timeout and output truncation to avoid hanging / oversized payloads.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any, Dict, Optional

from .base import AgentTool, ToolResult
from .timeout_defaults import resolve_timeout_ms_param


def _ensure_under_root(resolved: str, root: str) -> None:
    root = os.path.normpath(os.path.abspath(root))
    resolved = os.path.normpath(os.path.abspath(resolved))
    if not resolved.startswith(root):
        raise PermissionError(f"exec: cwd is outside workspace root: {root}")


class ExecTool(AgentTool):
    """Execute a shell command and return stdout/stderr/exit_code."""

    def __init__(self) -> None:
        super().__init__(
            name="exec",
            description=(
                "Execute a shell command in the workspace (high-risk, owner-only). "
                "Supports timeout_ms and optional cwd."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory (relative to workspace or absolute path).",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Optional timeout in milliseconds (default: 10000 or tools.timeout_ms in config, max: 120000).",
                    },
                    "max_output_chars": {
                        "type": "integer",
                        "description": "Optional max chars for stdout/stderr each (default: 12000).",
                    },
                },
                "required": ["command"],
            },
            owner_only=True,
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        workspace_dir = str((context or {}).get("workspace_dir") or os.getcwd())
        workspace_only = bool((context or {}).get("tools_fs_workspace_only") is True)

        command = params.get("command")
        command = command.strip() if isinstance(command, str) else ""
        if not command:
            return ToolResult(success=False, result={}, error="exec: command is required")

        cwd_raw = params.get("cwd")
        if isinstance(cwd_raw, str) and cwd_raw.strip():
            cwd = cwd_raw.strip()
            if not os.path.isabs(cwd):
                cwd = os.path.join(workspace_dir, cwd)
        else:
            cwd = workspace_dir
        cwd = os.path.normpath(os.path.abspath(cwd))
        if not os.path.isdir(cwd):
            return ToolResult(success=False, result={}, error=f"exec: cwd does not exist: {cwd}")
        if workspace_only:
            try:
                _ensure_under_root(cwd, workspace_dir)
            except PermissionError as e:
                return ToolResult(success=False, result={}, error=str(e))

        timeout_ms = resolve_timeout_ms_param(
            params,
            context,
            param_key="timeout_ms",
            default_ms=10000,
            min_ms=100,
            max_ms=120000,
        )

        max_output_chars = params.get("max_output_chars", 12000)
        try:
            max_output_chars = int(max_output_chars)
        except (TypeError, ValueError):
            max_output_chars = 12000
        max_output_chars = max(512, min(max_output_chars, 50000))

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            return ToolResult(success=False, result={}, error=f"exec: failed to start command: {e}")

        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                pass
            await asyncio.sleep(0.1)
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
            stdout_b, stderr_b = await proc.communicate()

        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        stdout_truncated = False
        stderr_truncated = False
        if len(stdout) > max_output_chars:
            stdout = stdout[:max_output_chars]
            stdout_truncated = True
        if len(stderr) > max_output_chars:
            stderr = stderr[:max_output_chars]
            stderr_truncated = True

        exit_code = proc.returncode if proc.returncode is not None else -1
        success = (exit_code == 0) and (not timed_out)
        result = {
            "command": command,
            "cwd": cwd,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        error = None
        if timed_out:
            error = f"exec: command timed out after {timeout_ms}ms"
        elif exit_code != 0:
            error = f"exec: command failed with exit code {exit_code}"
        return ToolResult(success=success, result=result, error=error)

