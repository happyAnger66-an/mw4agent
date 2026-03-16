"""Tool permission policy for MW4Agent (profile + allow + deny).

This is a simplified version of OpenClaw's tool-policy system, scoped to:
- tools.profile: named profile of tools to enable (minimal, coding, full)
- tools.allow: explicit allowlist (tool names or globs)
- tools.deny: explicit denylist (tool names or globs)
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Dict, Iterable, List, Optional, Sequence

from .base import AgentTool


@dataclass
class ToolPolicyConfig:
    """Config model for tools policy."""

    profile: str = "coding"
    allow: Optional[List[str]] = None
    deny: Optional[List[str]] = None


def _load_tool_policy_from_dict(raw: Dict) -> ToolPolicyConfig:
    """Load ToolPolicyConfig from a raw dict (from config manager)."""
    if not isinstance(raw, dict):
        return ToolPolicyConfig()
    profile = str(raw.get("profile") or "coding").strip().lower()
    allow_val = raw.get("allow")
    deny_val = raw.get("deny")
    allow: Optional[List[str]]
    deny: Optional[List[str]]
    if isinstance(allow_val, str):
        allow = [allow_val]
    elif isinstance(allow_val, Iterable):
        allow = [str(x) for x in allow_val]
    else:
        allow = None
    if isinstance(deny_val, str):
        deny = [deny_val]
    elif isinstance(deny_val, Iterable):
        deny = [str(x) for x in deny_val]
    else:
        deny = None
    return ToolPolicyConfig(profile=profile or "coding", allow=allow, deny=deny)


def resolve_tool_policy_config(cfg_manager) -> ToolPolicyConfig:
    """Resolve ToolPolicyConfig from the root config section "tools".

    Example config (~/.mw4agent/mw4agent.json):
    {
      "tools": {
        "profile": "coding",
        "allow": ["memory_search"],
        "deny": ["write", "memory_write"]
      }
    }
    """
    try:
        raw = cfg_manager.read_config("tools", default={})
    except Exception:
        raw = {}
    return _load_tool_policy_from_dict(raw)


def _match_any(name: str, patterns: Sequence[str]) -> bool:
    """Return True if name matches any glob pattern in patterns."""
    for pat in patterns:
        pat = (pat or "").strip()
        if not pat:
            continue
        # Exact name or glob via fnmatch
        if name == pat or fnmatch(name, pat):
            return True
    return False


def _profile_allow_list(profile: str) -> List[str]:
    """Return the base allow list for a named profile.

    Profiles are intentionally simple and based on tool *names*:
    - minimal: no tools (LLM-only)
    - coding: file + memory tools (read/write/memory_*)
    - full: all tools ("*")
    """
    p = (profile or "").strip().lower()
    if p == "minimal":
        return []
    if p == "full":
        return ["*"]
    # Default "coding" profile.
    return [
        "read",
        "write",
        "memory_search",
        "memory_get",
        "memory_write",
    ]


def filter_tools_by_policy(
    tools: Sequence[AgentTool],
    policy: ToolPolicyConfig,
) -> List[AgentTool]:
    """Filter a list of AgentTool according to ToolPolicyConfig.

    Precedence:
    1) deny has highest priority
    2) profile defines a base allow set
    3) allow extends/overrides the profile base
    4) if effective allow is ["*"], all non-denied tools are allowed
    """
    deny = policy.deny or []
    base_allow = _profile_allow_list(policy.profile)
    extra_allow = policy.allow or []
    # Merge base allow + explicit allow.
    effective_allow = list(dict.fromkeys(base_allow + extra_allow))

    out: List[AgentTool] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str):
            continue
        # 1) deny: if matches, always excluded.
        if deny and _match_any(name, deny):
            continue
        # 2) allow: if any pattern is "*", everything not denied is allowed.
        if effective_allow and any(pat == "*" for pat in effective_allow):
            out.append(tool)
            continue
        # 3) otherwise need to match allow list; empty allow means "no tools".
        if effective_allow and _match_any(name, effective_allow):
            out.append(tool)
    return out

