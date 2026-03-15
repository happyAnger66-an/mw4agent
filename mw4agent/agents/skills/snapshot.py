"""Skills snapshot utilities for attaching skills to sessions and prompts."""

from __future__ import annotations

from typing import Any, Dict, List

from ...skills import get_default_skill_manager
from ...plugin.loader import get_plugin_skill_source


def build_skill_snapshot() -> Dict[str, Any]:
    """Build a minimal skills snapshot from all known skills (default manager + plugin skills).

    Plugin skills are merged after default skills; duplicate names are resolved by
    keeping the default (main) skill so main wins.

    Snapshot 结构（示例）：
    {
      "skills": [
        {"name": "fs_list", "description": "List files in a directory"},
        ...
      ],
      "count": 1,
      "prompt": "Available skills:\\n- fs_list: List files in a directory",
    }
    """
    mgr = get_default_skill_manager()
    main_skills: Dict[str, Dict[str, Any]] = mgr.read_all_skills()
    plugin_skills: Dict[str, Dict[str, Any]] = get_plugin_skill_source().read_all_skills()
    # Main wins on duplicate name: start with main, then add plugin-only names
    all_skills: Dict[str, Dict[str, Any]] = dict(main_skills)
    for k, v in plugin_skills.items():
        if k not in all_skills:
            all_skills[k] = v
    items: List[Dict[str, Any]] = []
    lines: List[str] = []

    for name, spec in sorted(all_skills.items()):
        desc = ""
        if isinstance(spec, dict):
            raw_desc = spec.get("description") or spec.get("desc") or ""
            if isinstance(raw_desc, str):
                desc = raw_desc.strip()
        item: Dict[str, Any] = {"name": name}
        if desc:
            item["description"] = desc
        items.append(item)
        if desc:
            lines.append(f"- {name}: {desc}")
        else:
            lines.append(f"- {name}")

    prompt = ""
    if lines:
        prompt = "Available skills:\n" + "\n".join(lines)

    return {
        "skills": items,
        "count": len(items),
        "prompt": prompt,
    }

