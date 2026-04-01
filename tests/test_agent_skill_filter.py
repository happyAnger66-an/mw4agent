"""Tests for per-agent skills allowlist intersection (plan B)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_config_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "mw"
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(root / "state"))
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(root / "config"))
    return root


def _write_root_skills_config(config_dir: Path, skills_section: dict) -> None:
    cfg_path = config_dir / "mw4agent.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"skills": skills_section}
    cfg_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_agent_json(state_dir: Path, agent_id: str, agent_payload: dict) -> None:
    path = state_dir / "agents" / agent_id / "agent.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agent_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_effective_filter_intersects_global_and_agent(isolated_config_and_state: Path) -> None:
    from mw4agent.agents.skills.snapshot import resolve_effective_skill_filter_for_agent

    _write_root_skills_config(isolated_config_and_state / "config", {"filter": ["a", "b"]})
    _write_agent_json(isolated_config_and_state / "state", "main", {"skills": ["b", "c"]})

    assert resolve_effective_skill_filter_for_agent("main") == ["b"]


def test_effective_filter_only_global(isolated_config_and_state: Path) -> None:
    from mw4agent.agents.skills.snapshot import resolve_effective_skill_filter_for_agent

    _write_root_skills_config(isolated_config_and_state / "config", {"filter": ["a", "b"]})
    _write_agent_json(isolated_config_and_state / "state", "main", {"llm": {"provider": "echo"}})

    assert resolve_effective_skill_filter_for_agent("main") == ["a", "b"]


def test_effective_filter_only_agent(isolated_config_and_state: Path) -> None:
    from mw4agent.agents.skills.snapshot import resolve_effective_skill_filter_for_agent

    _write_root_skills_config(isolated_config_and_state / "config", {})
    _write_agent_json(isolated_config_and_state / "state", "main", {"skills": ["x", "y"]})

    assert resolve_effective_skill_filter_for_agent("main") == ["x", "y"]


def test_effective_filter_agent_explicit_empty_blocks_all(isolated_config_and_state: Path) -> None:
    from mw4agent.agents.skills.snapshot import resolve_effective_skill_filter_for_agent

    _write_root_skills_config(isolated_config_and_state / "config", {"filter": ["a", "b"]})
    _write_agent_json(isolated_config_and_state / "state", "main", {"skills": []})

    assert resolve_effective_skill_filter_for_agent("main") == []


def test_effective_filter_trims_and_dedupes(isolated_config_and_state: Path) -> None:
    from mw4agent.agents.skills.snapshot import resolve_effective_skill_filter_for_agent

    _write_root_skills_config(isolated_config_and_state / "config", {"filter": [" a ", "b", "b"]})
    _write_agent_json(isolated_config_and_state / "state", "main", {"skills": ["b", " ", "b", "c"]})

    assert resolve_effective_skill_filter_for_agent("main") == ["b"]


def test_global_empty_list_means_no_global_filter(isolated_config_and_state: Path) -> None:
    from mw4agent.agents.skills.snapshot import resolve_effective_skill_filter_for_agent

    _write_root_skills_config(isolated_config_and_state / "config", {"filter": []})
    _write_agent_json(isolated_config_and_state / "state", "main", {"skills": ["x"]})

    assert resolve_effective_skill_filter_for_agent("main") == ["x"]

