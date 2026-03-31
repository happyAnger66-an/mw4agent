"""Tool permission policy for MW4Agent (profile + allow + deny).

This is a simplified version of OpenClaw's tool-policy system, scoped to:
- tools.profile: named profile of tools to enable (minimal, coding, full)
- tools.allow: explicit allowlist (tool names or globs)
- tools.deny: explicit denylist (tool names or globs)
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .base import AgentTool


@dataclass
class ToolPolicyConfig:
    """Config model for tools policy."""

    profile: str = "coding"
    allow: Optional[List[str]] = None
    deny: Optional[List[str]] = None


@dataclass
class SandboxToolPolicy:
    """Sandbox tool policy applied on top of normal tool policy.

    Semantics (aligned with docs/openclaw/sandbox.md):
    - deny always wins
    - allow empty/None means blacklist-only mode (everything not denied is allowed)
    - allow non-empty means whitelist mode (must match allow and not match deny)

    Directory isolation: when active, read/write/exec/process tools use a per-session
    directory under tools.sandbox.workspaceRoot (see sandbox_workspace.py).

    execution_isolation: reserved for future WASM / other backends ("none" | "wasm").
    """

    enabled: bool = False
    allow: Optional[List[str]] = None
    deny: Optional[List[str]] = None
    directory_isolation: Optional[bool] = None
    execution_isolation: str = "none"

    def should_isolate_directories(self, *, run_sandbox_request: bool) -> bool:
        """Whether to use a sandbox session directory for FS tools this run."""
        active = bool(self.enabled or run_sandbox_request)
        if not active:
            return False
        if self.directory_isolation is False:
            return False
        if self.directory_isolation is True:
            return True
        # None => default: isolate whenever the sandbox layer is active for this run
        return True


def _parse_execution_isolation(raw: Dict) -> str:
    ei = raw.get("executionIsolation")
    if ei is None:
        ei = raw.get("execution_isolation")
    s = str(ei or "none").strip().lower()
    if s in ("none", "wasm"):
        return s
    return "none"


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


def _load_sandbox_policy_from_dict(raw: Dict) -> SandboxToolPolicy:
    if not isinstance(raw, dict):
        return SandboxToolPolicy()
    enabled = bool(raw.get("enabled") is True)
    allow_val = raw.get("allow")
    deny_val = raw.get("deny")
    allow: Optional[List[str]]
    deny: Optional[List[str]]
    di_raw = raw.get("directoryIsolation")
    if di_raw is None:
        di_raw = raw.get("directory_isolation")
    directory_isolation: Optional[bool] = di_raw if isinstance(di_raw, bool) else None
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
    return SandboxToolPolicy(
        enabled=enabled,
        allow=allow,
        deny=deny,
        directory_isolation=directory_isolation,
        execution_isolation=_parse_execution_isolation(raw),
    )


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


def resolve_sandbox_tool_policy_config(cfg_manager) -> SandboxToolPolicy:
    """Resolve SandboxToolPolicy from tools.sandbox."""
    try:
        raw_tools = cfg_manager.read_config("tools", default={})
    except Exception:
        raw_tools = {}
    if not isinstance(raw_tools, dict):
        return SandboxToolPolicy()
    raw = raw_tools.get("sandbox") or {}
    return _load_sandbox_policy_from_dict(raw)


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
    - coding: file + memory tools (read/write/memory_*) plus optional Feishu 文档插件工具 (feishu_*)
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
        # feishu-docs 等插件注册的工具名均以 feishu_ 开头；未加载插件时 registry 中不存在，无影响
        "feishu_*",
    ]


def _load_raw_tools_config(cfg_manager) -> Dict:
    """Low-level helper: read raw 'tools' config dict (never raises)."""
    try:
        raw = cfg_manager.read_config("tools", default={})
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _lookup_nested_policy(
    tools_cfg: Dict,
    *,
    channel: Optional[str],
    user_id: Optional[str],
    sender_is_owner: Optional[bool],
) -> Optional[ToolPolicyConfig]:
    """Lookup a more specific ToolPolicyConfig based on channel/user.

    Precedence:
    1) by_channel_user["<channel>:<user_id>"]
    2) by_user["owner:<user_id>"] / by_user["user:<user_id>"] (and owner:* / user:*)
    3) by_channel["<channel>"]
    Returns None when no override is found.
    """
    if not isinstance(tools_cfg, dict):
        return None

    by_channel_user = tools_cfg.get("by_channel_user") or {}
    by_user = tools_cfg.get("by_user") or {}
    by_channel = tools_cfg.get("by_channel") or {}

    # 1) channel+user override
    if channel and user_id and isinstance(by_channel_user, dict):
        key = f"{channel}:{user_id}"
        raw = by_channel_user.get(key)
        if isinstance(raw, dict):
            return _load_tool_policy_from_dict(raw)

    # 2) user-level override (owner vs normal user)
    if user_id and isinstance(by_user, dict):
        if sender_is_owner:
            raw = by_user.get(f"owner:{user_id}") or by_user.get("owner:*")
        else:
            raw = by_user.get(f"user:{user_id}") or by_user.get("user:*")
        if isinstance(raw, dict):
            return _load_tool_policy_from_dict(raw)

    # 3) channel-level override
    if channel and isinstance(by_channel, dict):
        raw = by_channel.get(channel)
        if isinstance(raw, dict):
            return _load_tool_policy_from_dict(raw)
        # feishu:sales 等子通道未单独配置时回退到 feishu
        if (
            raw is None
            and isinstance(channel, str)
            and channel.startswith("feishu:")
            and channel != "feishu"
        ):
            raw_fb = by_channel.get("feishu")
            if isinstance(raw_fb, dict):
                return _load_tool_policy_from_dict(raw_fb)

    return None


def resolve_effective_policy_for_context(
    cfg_manager,
    *,
    base_policy: ToolPolicyConfig,
    channel: Optional[str],
    user_id: Optional[str],
    sender_is_owner: Optional[bool],
    command_authorized: Optional[bool],
) -> ToolPolicyConfig:
    """Resolve the effective ToolPolicyConfig for one agent run.

    - Starts from base_policy (global tools.profile/allow/deny).
    - Optionally overrides with tools.by_channel_user / by_user / by_channel.
    - command_authorized is currently passed through for future use; it does not
      further restrict the policy yet.
    """
    tools_cfg = _load_raw_tools_config(cfg_manager)
    override = _lookup_nested_policy(
        tools_cfg,
        channel=channel,
        user_id=user_id,
        sender_is_owner=sender_is_owner,
    )
    if override is not None:
        return override
    return base_policy


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


def is_tool_allowed_by_sandbox(policy: SandboxToolPolicy, name: str) -> bool:
    """Return True if tool name is allowed by sandbox policy."""
    if not policy.enabled:
        return True

    deny = policy.deny or []
    allow = policy.allow or []

    # 1) deny always wins
    if deny and _match_any(name, deny):
        return False

    # 2) allow empty => blacklist-only mode
    if not allow:
        return True

    # 3) allow non-empty => whitelist mode
    return _match_any(name, allow)


def filter_tools_by_sandbox_policy(
    tools: Sequence[AgentTool],
    policy: SandboxToolPolicy,
) -> List[AgentTool]:
    """Filter tools by sandbox policy (deny first; allow semantics per OpenClaw)."""
    if not policy.enabled:
        return list(tools)
    out: List[AgentTool] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str):
            continue
        if is_tool_allowed_by_sandbox(policy, name):
            out.append(tool)
    return out


def resolve_effective_allow_patterns(policy: ToolPolicyConfig) -> List[str]:
    """Return the expanded allow patterns after applying profile + explicit allow."""
    base_allow = _profile_allow_list(policy.profile)
    extra_allow = policy.allow or []
    return list(dict.fromkeys(base_allow + extra_allow))

