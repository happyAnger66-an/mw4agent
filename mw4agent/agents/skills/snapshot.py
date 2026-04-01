"""Skills snapshot utilities for attaching skills to sessions and prompts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...config.root import read_root_section
from ..agent_manager import AgentManager
from ...plugin.loader import get_plugin_skill_source
from ...skills import SkillManager, get_default_skill_manager


def _normalize_skill_filter(skill_filter: Optional[Sequence[str]]) -> Optional[List[str]]:
    if not skill_filter:
        return None
    normalized: List[str] = []
    for item in skill_filter:
        val = str(item or "").strip()
        if val and val not in normalized:
            normalized.append(val)
    return normalized or None


def _normalize_skill_filter_keep_empty(skill_filter: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Like _normalize_skill_filter but preserves explicit empty list as [].

    Used for per-agent skills allowlists where [] means the agent sees no skills.
    """
    if skill_filter is None:
        return None
    normalized: List[str] = []
    for item in skill_filter:
        val = str(item or "").strip()
        if val and val not in normalized:
            normalized.append(val)
    return normalized


def resolve_global_skill_filter() -> Optional[List[str]]:
    """Global skills.filter from ~/.mw4agent/mw4agent.json (normalized).

    Backward compatible: missing/empty filter means no filtering.
    """
    section = read_root_section("skills", default={})
    cfg_filter = section.get("filter") if isinstance(section, dict) else None
    return _normalize_skill_filter(cfg_filter if isinstance(cfg_filter, list) else None)


def resolve_agent_skill_filter(agent_id: Optional[str]) -> Optional[List[str]]:
    """Per-agent skills allowlist from ~/.mw4agent/agents/<agentId>/agent.json.

    If key exists and is empty list, returns [] (meaning no skills for this agent).
    If missing, returns None.
    """
    try:
        cfg = AgentManager().get(str(agent_id or "main"))
        if cfg is None:
            return None
        if cfg.skills is None:
            return None
        return _normalize_skill_filter_keep_empty(cfg.skills)
    except Exception:
        return None


def resolve_effective_skill_filter_for_agent(
    agent_id: Optional[str],
    *,
    skill_filter: Optional[Sequence[str]] = None,
) -> Optional[List[str]]:
    """Resolve effective skill filter using plan B (intersection)."""

    def _intersect(a: Optional[List[str]], b: Optional[List[str]]) -> Optional[List[str]]:
        if a is None:
            return b
        if b is None:
            return a
        if a == [] or b == []:
            return []
        bs = set(b)
        return [x for x in a if x in bs]

    caller = _normalize_skill_filter_keep_empty(skill_filter) if skill_filter is not None else None
    merged = _intersect(resolve_global_skill_filter(), resolve_agent_skill_filter(agent_id))
    merged = _intersect(merged, caller)
    return merged


def _resolve_workspace_skill_paths(workspace_dir: Optional[str]) -> List[Tuple[str, Path]]:
    if not workspace_dir:
        return []
    root = Path(workspace_dir).resolve()
    candidates = [
        ("workspace", root / "skills"),
        ("workspace", root / ".agents" / "skills"),
    ]
    return [(source, path) for source, path in candidates if path.is_dir()]


def _resolve_configured_skill_paths(workspace_dir: Optional[str] = None) -> List[Tuple[str, Path]]:
    section = read_root_section("skills", default={})
    load_cfg = section.get("load") if isinstance(section, dict) else {}
    raw_paths: List[Any] = []
    if isinstance(load_cfg, dict):
        configured_paths = load_cfg.get("paths")
        if isinstance(configured_paths, list):
            raw_paths.extend(configured_paths)
        extra_dirs = load_cfg.get("extra_dirs")
        if isinstance(extra_dirs, list):
            raw_paths.extend(extra_dirs)

    result: List[Tuple[str, Path]] = []
    for item in raw_paths:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if not path.is_absolute() and workspace_dir:
            path = Path(workspace_dir) / path
        path = path.resolve()
        if path.is_dir():
            result.append(("config", path))
    return result


def _resolve_home_skill_path() -> Optional[Tuple[str, Path]]:
    env_dir = os.environ.get("MW4AGENT_SKILLS_DIR", "").strip()
    if env_dir:
        env_path = Path(env_dir).expanduser().resolve()
        if env_path.is_dir():
            return ("home", env_path)
    mgr = get_default_skill_manager()
    if isinstance(mgr, SkillManager) and mgr.skills_dir.is_dir():
        return ("home", mgr.skills_dir.resolve())
    return None


def _collect_skills_from_paths(
    paths: List[Tuple[str, Path]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    all_skills: Dict[str, Dict[str, Any]] = {}
    source_by_name: Dict[str, str] = {}
    location_by_name: Dict[str, str] = {}
    for source_kind, path in paths:
        mgr = SkillManager(skills_dir=str(path))
        for name in mgr.list_skills():
            spec = mgr.read_skill(name)
            if not isinstance(spec, dict):
                continue
            if name in all_skills:
                continue
            all_skills[name] = spec
            source_by_name[name] = source_kind
            resolved = mgr._resolve_skill_path(name)  # type: ignore[attr-defined]
            if resolved is not None:
                location_by_name[name] = str(resolved[0])
    return all_skills, source_by_name, location_by_name


def _resolve_skill_limits() -> Dict[str, int]:
    section = read_root_section("skills", default={})
    limits = section.get("limits") if isinstance(section, dict) else {}
    if not isinstance(limits, dict):
        limits = {}

    def _read_int(primary: str, fallback: str, default_value: int, min_value: int, max_value: int) -> int:
        raw = limits.get(primary, limits.get(fallback, default_value))
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = default_value
        return max(min_value, min(max_value, val))

    return {
        "max_skills_in_prompt": _read_int("maxSkillsInPrompt", "max_skills_in_prompt", 150, 1, 2000),
        "max_skills_prompt_chars": _read_int("maxSkillsPromptChars", "max_skills_prompt_chars", 30000, 500, 500000),
    }


def _format_skills_prompt(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = [
        "The following skills provide specialized instructions.",
        "Use skill names/descriptions/locations to decide which one to read or invoke.",
        "",
        "<available_skills>",
    ]
    for item in items:
        lines.append("  <skill>")
        lines.append(f"    <name>{item.get('name')}</name>")
        if item.get("description"):
            lines.append(f"    <description>{item.get('description')}</description>")
        if item.get("location"):
            lines.append(f"    <location>{item.get('location')}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def _format_skills_prompt_compact(items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = [
        "Skills catalog (compact mode, descriptions omitted):",
        "",
        "<available_skills>",
    ]
    for item in items:
        lines.append("  <skill>")
        lines.append(f"    <name>{item.get('name')}</name>")
        if item.get("location"):
            lines.append(f"    <location>{item.get('location')}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def _apply_prompt_budget(
    items: List[Dict[str, Any]],
    *,
    max_skills_in_prompt: int,
    max_skills_prompt_chars: int,
) -> Tuple[List[Dict[str, Any]], bool, bool]:
    limited = items[:max_skills_in_prompt]
    truncated = len(items) > len(limited)
    compact = False
    if len(_format_skills_prompt(limited)) <= max_skills_prompt_chars:
        return limited, truncated, compact
    compact = True
    if len(_format_skills_prompt_compact(limited)) <= max_skills_prompt_chars:
        return limited, truncated, compact

    lo = 0
    hi = len(limited)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(_format_skills_prompt_compact(limited[:mid])) <= max_skills_prompt_chars:
            lo = mid
        else:
            hi = mid - 1
    return limited[:lo], True, compact


def build_skill_snapshot(
    *,
    workspace_dir: Optional[str] = None,
    skill_filter: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Build a skills snapshot from workspace/config/home/plugin sources."""
    paths: List[Tuple[str, Path]] = []
    paths.extend(_resolve_workspace_skill_paths(workspace_dir))
    paths.extend(_resolve_configured_skill_paths(workspace_dir))
    home_path = _resolve_home_skill_path()
    if home_path is not None:
        paths.append(home_path)

    all_skills, source_by_name, location_by_name = _collect_skills_from_paths(paths)
    plugin_skills: Dict[str, Dict[str, Any]] = get_plugin_skill_source().read_all_skills()
    for name, spec in plugin_skills.items():
        if name in all_skills:
            continue
        all_skills[name] = spec
        source_by_name[name] = "plugin"
        location_by_name[name] = "<plugin>"

    # Backward compatible: caller filter overrides global config filter when non-empty.
    merged_filter = _normalize_skill_filter(skill_filter) or resolve_global_skill_filter()

    items: List[Dict[str, Any]] = []
    filtered_out: List[str] = []
    for name, spec in sorted(all_skills.items()):
        if merged_filter is not None and name not in merged_filter:
            filtered_out.append(name)
            continue
        desc = ""
        if isinstance(spec, dict):
            raw_desc = spec.get("description") or spec.get("desc") or ""
            if isinstance(raw_desc, str):
                desc = raw_desc.strip()
        item: Dict[str, Any] = {
            "name": name,
            "source": source_by_name.get(name, "unknown"),
            "location": location_by_name.get(name, ""),
        }
        if desc:
            item["description"] = desc
        items.append(item)

    limits = _resolve_skill_limits()
    skills_for_prompt, prompt_truncated, prompt_compact = _apply_prompt_budget(
        items,
        max_skills_in_prompt=limits["max_skills_in_prompt"],
        max_skills_prompt_chars=limits["max_skills_prompt_chars"],
    )
    prompt_body = (
        _format_skills_prompt_compact(skills_for_prompt)
        if prompt_compact
        else _format_skills_prompt(skills_for_prompt)
    )
    if prompt_truncated:
        prompt_note = (
            f"⚠️ Skills truncated: included {len(skills_for_prompt)} of {len(items)}"
            + (" (compact mode)." if prompt_compact else ".")
        )
        prompt = prompt_note + "\n" + prompt_body
    elif prompt_compact:
        prompt = "⚠️ Skills catalog using compact mode.\n" + prompt_body
    else:
        prompt = prompt_body
    source_stats: Dict[str, int] = {}
    for item in items:
        source = str(item.get("source") or "unknown")
        source_stats[source] = source_stats.get(source, 0) + 1
    sources = [{"name": k, "count": source_stats[k]} for k in sorted(source_stats.keys())]

    version_payload = {
        "skills": [
            {"name": i.get("name"), "description": i.get("description"), "source": i.get("source")}
            for i in items
        ],
        "skill_filter": merged_filter or [],
    }
    version = hashlib.sha1(
        json.dumps(version_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]

    return {
        "skills": items,
        "count": len(items),
        "prompt": prompt,
        "prompt_count": len(skills_for_prompt),
        "prompt_truncated": prompt_truncated,
        "prompt_compact": prompt_compact,
        "version": version,
        "skill_filter": merged_filter or [],
        "sources": sources,
        "filtered_out": sorted(filtered_out),
    }

