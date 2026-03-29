"""Global default tool timeouts from root config (tools section).

When set, ``default_tool_timeout_ms`` is injected into ``tool_context`` by AgentRunner.
Individual tools use it only when the tool call does not specify a timeout parameter.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ...config.root import read_root_section


def resolve_default_tool_timeout_ms() -> Optional[int]:
    """Read global default from env or ``tools`` section of ``mw4agent.json``.

    Supported keys (first non-empty wins): ``timeout_ms``, ``timeoutMs``, ``defaultTimeoutMs``,
    ``default_timeout_ms``.

    Env: ``MW4AGENT_TOOLS_TIMEOUT_MS`` (integer milliseconds, > 0).
    """
    env = os.environ.get("MW4AGENT_TOOLS_TIMEOUT_MS", "").strip()
    if env:
        try:
            v = int(env)
            if v > 0:
                return v
        except ValueError:
            pass

    tools = read_root_section("tools", default={})
    if not isinstance(tools, dict):
        return None
    for key in ("timeout_ms", "timeoutMs", "defaultTimeoutMs", "default_timeout_ms"):
        raw = tools.get(key)
        if raw is None:
            continue
        try:
            ms = int(raw)
        except (TypeError, ValueError):
            continue
        if ms > 0:
            return ms
    return None


def resolve_timeout_ms_param(
    params: Dict[str, Any],
    context: Optional[Dict[str, Any]],
    *,
    param_key: str,
    default_ms: int,
    min_ms: int,
    max_ms: int,
) -> int:
    """Resolve timeout: explicit tool arg > context ``default_tool_timeout_ms`` > *default_ms*."""
    raw = params.get(param_key)
    if raw is None and context is not None:
        raw = context.get("default_tool_timeout_ms")
    if raw is None:
        n = default_ms
    else:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = default_ms
    return max(min_ms, min(max_ms, n))
