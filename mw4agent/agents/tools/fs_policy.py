"""Filesystem tool policy (workspace-only guard).

Aligned with OpenClaw:
- tools.profile controls which tools are exposed to the model.
- tools.fs.workspaceOnly controls whether filesystem tools are restricted to the workspace root.

Default is workspaceOnly=false (unrestricted), matching OpenClaw's legacy behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ToolFsPolicyConfig:
    workspace_only: bool = False


def _load_fs_policy_from_dict(raw: Any) -> ToolFsPolicyConfig:
    if not isinstance(raw, dict):
        return ToolFsPolicyConfig()
    # Support both "workspaceOnly" (OpenClaw-style) and "workspace_only" (py style).
    wo = raw.get("workspaceOnly")
    if wo is None:
        wo = raw.get("workspace_only")
    return ToolFsPolicyConfig(workspace_only=bool(wo) if isinstance(wo, bool) else False)


def resolve_tool_fs_policy_config(cfg_manager) -> ToolFsPolicyConfig:
    """Resolve ToolFsPolicyConfig from root config section 'tools.fs'."""
    try:
        tools = cfg_manager.read_config("tools", default={})
    except Exception:
        tools = {}
    fs_cfg: Optional[Dict[str, Any]] = None
    if isinstance(tools, dict):
        fs_val = tools.get("fs")
        if isinstance(fs_val, dict):
            fs_cfg = fs_val
    return _load_fs_policy_from_dict(fs_cfg or {})

