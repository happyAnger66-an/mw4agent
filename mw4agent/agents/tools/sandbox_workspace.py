"""Sandbox session workspace directories (host FS isolation for tools).

Reserved for future WASM / other execution isolation via SandboxToolPolicy.execution_isolation.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from ...config.paths import get_state_dir, normalize_agent_id


def _read_tools_sandbox_dict(cfg_manager) -> Dict[str, Any]:
    try:
        tools = cfg_manager.read_config("tools", default={})
    except Exception:
        tools = {}
    if not isinstance(tools, dict):
        return {}
    raw = tools.get("sandbox") or {}
    return raw if isinstance(raw, dict) else {}


def resolve_sandbox_sessions_root(cfg_manager) -> str:
    """Root directory for per-run sandbox working dirs (~/.mw4agent/sandbox-sessions by default)."""
    raw = _read_tools_sandbox_dict(cfg_manager)
    root = raw.get("workspaceRoot") or raw.get("workspace_root")
    if isinstance(root, str) and root.strip():
        return os.path.abspath(os.path.expanduser(root.strip()))
    env = os.environ.get("MW4AGENT_SANDBOX_WORKSPACE_DIR") or os.environ.get(
        "MW4AGENT_SANDBOX_SESSIONS_DIR"
    )
    if env and env.strip():
        return os.path.abspath(os.path.expanduser(env.strip()))
    return os.path.join(get_state_dir(), "sandbox-sessions")


def sanitize_sandbox_session_dirname(session_id: str) -> str:
    """Sanitize session id for use as a single path component (defense in depth)."""
    sid = (session_id or "").strip()
    if not sid or len(sid) > 128:
        raise ValueError("sandbox: invalid session_id")
    if ".." in sid or sid.startswith("."):
        raise ValueError("sandbox: session_id must not contain '..' or start with '.'")
    if not all(c.isalnum() or c in "-_" for c in sid):
        raise ValueError("sandbox: session_id must be alphanumeric, hyphen, or underscore")
    return sid


def ensure_sandbox_tool_workspace(
    *,
    cfg_manager,
    agent_id: Optional[str],
    session_id: str,
) -> Tuple[str, str]:
    """Create and return (sandbox_root, tool_workspace_dir).

    tool_workspace_dir is: <root>/<agentId>/<sessionId>/
    """
    root = resolve_sandbox_sessions_root(cfg_manager)
    os.makedirs(root, exist_ok=True)
    root_real = os.path.realpath(root)
    aid = normalize_agent_id(agent_id)
    sess = sanitize_sandbox_session_dirname(session_id)
    tool_ws = os.path.join(root_real, aid, sess)
    os.makedirs(tool_ws, exist_ok=True)
    tool_real = os.path.realpath(tool_ws)
    if tool_real != root_real and not tool_real.startswith(root_real + os.sep):
        raise PermissionError("sandbox: resolved tool path escapes sandbox root")
    return root_real, tool_real
