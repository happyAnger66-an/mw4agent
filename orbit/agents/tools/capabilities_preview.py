"""Effective tool and skill lists for orchestration UI inspection (read-only)."""

from __future__ import annotations

from typing import Any, Dict, List

from ...config import get_default_config_manager
from ..skills.snapshot import build_skill_snapshot, resolve_effective_skill_filter_for_agent
from .policy import (
    filter_tools_by_policy,
    filter_tools_by_sandbox_policy,
    resolve_effective_policy_for_context,
    resolve_sandbox_tool_policy_config,
    resolve_tool_policy_config,
)
from .registry import get_tool_registry
from .web_fetch_tool import is_web_fetch_enabled
from .web_search_tool import is_web_search_enabled


def list_effective_tool_names_for_orchestrator_turn() -> List[str]:
    """Match ``AgentRunner._execute_agent_turn_inner`` tool exposure for ``channel=orchestrator``.

    Orchestration runs omit ``sender_is_owner`` / ``sandbox`` on :class:`AgentRunParams`, so
    ``owner_only`` tools are hidden and sandbox follows global config only.
    """
    cfg_mgr = get_default_config_manager()
    base_policy = resolve_tool_policy_config(cfg_mgr)
    effective_policy = resolve_effective_policy_for_context(
        cfg_mgr,
        base_policy=base_policy,
        channel="orchestrator",
        user_id=None,
        sender_is_owner=None,
        command_authorized=None,
    )
    sandbox_policy = resolve_sandbox_tool_policy_config(cfg_mgr)

    registry = get_tool_registry()
    all_tools = registry.list_tools()
    tools_after_policy = filter_tools_by_policy(all_tools, effective_policy)
    # Orchestration does not set ``sender_is_owner=True``, so owner-only tools are hidden.
    tools_after_policy = [t for t in tools_after_policy if not getattr(t, "owner_only", False)]
    if not is_web_search_enabled():
        tools_after_policy = [t for t in tools_after_policy if t.name != "web_search"]
    if not is_web_fetch_enabled():
        tools_after_policy = [t for t in tools_after_policy if t.name != "web_fetch"]
    tools_after_policy = filter_tools_by_sandbox_policy(tools_after_policy, sandbox_policy)
    names = sorted({str(t.name) for t in tools_after_policy if getattr(t, "name", None)})
    return names


def build_skills_inspect_for_orchestration_agent(
    *,
    agent_id: str,
    workspace_dir: str,
) -> Dict[str, Any]:
    """Skills visible to the agent in orchestration (orch workspace + same filter as runner)."""
    filt = resolve_effective_skill_filter_for_agent(agent_id)
    snap = build_skill_snapshot(workspace_dir=workspace_dir, skill_filter=filt)
    items = snap.get("skills") or []
    compact: List[Dict[str, Any]] = []
    for i in items:
        if not isinstance(i, dict):
            continue
        row: Dict[str, Any] = {
            "name": i.get("name"),
            "source": i.get("source"),
        }
        desc = i.get("description")
        if isinstance(desc, str) and desc.strip():
            row["description"] = desc.strip()
        compact.append(row)
    return {
        "skills": compact,
        "skillsCount": int(snap.get("count") or 0),
        "skillsPromptCount": int(snap.get("prompt_count") or 0),
        "skillsPromptTruncated": bool(snap.get("prompt_truncated")),
        "skillsPromptCompact": bool(snap.get("prompt_compact")),
    }
