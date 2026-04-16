"""Tests for *-skill bundle layout: ``<bundle>-skill/<child>/SKILL.md``."""

from __future__ import annotations

import json
from pathlib import Path

from orbit.agents.skills.snapshot import build_skill_snapshot
from orbit.config.root import write_root_config
from orbit.skills import SkillManager


def test_list_and_read_bundle_child_skills(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    bundle = root / "optimized-skill"
    child_a = bundle / "cuda-kernels"
    child_b = bundle / "memory-patterns"
    child_a.mkdir(parents=True)
    child_b.mkdir(parents=True)
    (child_a / "SKILL.md").write_text(
        "---\ndescription: CUDA helpers\n---\n# A\n",
        encoding="utf-8",
    )
    (child_b / "SKILL.md").write_text(
        "---\nname: custom-b\ndescription: Child B\n---\n",
        encoding="utf-8",
    )

    mgr = SkillManager(skills_dir=str(root))
    names = mgr.list_skills()
    assert "optimized-skill/cuda-kernels" in names
    assert "optimized-skill/memory-patterns" in names

    a = mgr.read_skill("optimized-skill/cuda-kernels")
    assert a is not None
    assert a.get("description") == "CUDA helpers"
    assert a.get("name") == "optimized-skill/cuda-kernels"

    b = mgr.read_skill("optimized-skill/memory-patterns")
    assert b is not None
    assert b.get("name") == "custom-b"
    assert b.get("description") == "Child B"


def test_bundle_parent_with_skill_md_is_single_skill_not_children(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    bundle = root / "meta-skill"
    bundle.mkdir(parents=True)
    (bundle / "SKILL.md").write_text("---\ndescription: Parent only\n---\n", encoding="utf-8")
    child = bundle / "ignored-child"
    child.mkdir()
    (child / "SKILL.md").write_text("---\ndescription: Should not list\n---\n", encoding="utf-8")

    mgr = SkillManager(skills_dir=str(root))
    names = mgr.list_skills()
    assert names == ["meta-skill"]
    assert mgr.read_skill("meta-skill/ignored-child") is None


def test_nested_bundle_json_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    mgr = SkillManager(skills_dir=str(root))
    bundle = mgr.skills_dir / "data-skill"
    bundle.mkdir()
    payload = {"name": "nested-json", "description": "from json"}
    (bundle / "loader.json").write_text(json.dumps(payload), encoding="utf-8")
    assert mgr.list_skills() == ["data-skill/loader"]
    assert mgr.read_skill("data-skill/loader") == payload


def test_build_skill_snapshot_includes_bundle_skills(tmp_path: Path, monkeypatch) -> None:
    cfg_dir = tmp_path / "cfg"
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("ORBIT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.delenv("ORBIT_SKILLS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ORBIT_STATE_DIR", str(tmp_path / "orbit_state"))

    ws_skills = workspace / "skills" / "gpu-skill"
    (ws_skills / "smem").mkdir(parents=True)
    (ws_skills / "smem" / "SKILL.md").write_text(
        "---\ndescription: Shared memory\n---\n",
        encoding="utf-8",
    )

    write_root_config({"skills": {}})
    snap = build_skill_snapshot(workspace_dir=str(workspace))
    names = [s["name"] for s in snap["skills"]]
    assert "gpu-skill/smem" in names
