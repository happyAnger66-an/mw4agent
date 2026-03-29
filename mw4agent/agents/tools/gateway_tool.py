"""Gateway tools for MW4Agent (OpenClaw-inspired).

This module mirrors the OpenClaw idea:
- agent tool resolves gateway connection options
- calls gateway RPC with a least-surprise interface
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ...gateway.client import call_rpc
from ..tools.base import AgentTool, ToolResult


@dataclass(frozen=True)
class GatewayCallOptions:
    base_url: str
    timeout_ms: int = 30_000


def resolve_gateway_options(context: Optional[Dict[str, Any]] = None) -> GatewayCallOptions:
    # Priority: tool execution context -> env -> default
    ctx_url = None
    if context and isinstance(context.get("gateway_base_url"), str):
        ctx_url = context["gateway_base_url"].strip() or None
    env_url = os.getenv("MW4AGENT_GATEWAY_URL", "").strip() or None
    timeout_ms = 30_000
    if context:
        raw = context.get("default_tool_timeout_ms")
        if raw is not None:
            try:
                timeout_ms = max(1, int(raw))
            except (TypeError, ValueError):
                pass
    return GatewayCallOptions(
        base_url=ctx_url or env_url or "http://127.0.0.1:18790",
        timeout_ms=timeout_ms,
    )


class GatewayLsTool(AgentTool):
    """Call gateway 'ls' method and return entries."""

    def __init__(self) -> None:
        super().__init__(
            name="gateway_ls",
            description="List directory entries via the Gateway (RPC method: ls).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to list (default: '.')",
                    },
                    "timeoutMs": {
                        "type": "number",
                        "description": "RPC timeout in milliseconds (optional)",
                    },
                },
            },
            owner_only=True,
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        opts = resolve_gateway_options(context)
        path = str(params.get("path") or ".").strip() or "."
        timeout_ms = params.get("timeoutMs")
        try:
            timeout_ms_int = int(timeout_ms) if timeout_ms is not None else opts.timeout_ms
        except Exception:
            timeout_ms_int = opts.timeout_ms
        timeout_ms_int = max(1, timeout_ms_int)
        try:
            res = call_rpc(base_url=opts.base_url, method="ls", params={"path": path}, timeout_ms=timeout_ms_int)
            if res.get("ok") is not True:
                return ToolResult(success=False, result=res, error=str((res.get("error") or {}).get("message") or "gateway error"))
            return ToolResult(success=True, result=res.get("payload") or res)
        except Exception as e:
            return ToolResult(success=False, result={"error": str(e)}, error=str(e))

