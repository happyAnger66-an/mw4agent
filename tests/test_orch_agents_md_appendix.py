"""Orchestration-root AGENTS.md appended to extra system prompt."""

import os

from mw4agent.memory.bootstrap import load_orchestration_team_agents_appendix


def test_load_orchestration_team_agents_appendix_missing(tmp_path) -> None:
    assert load_orchestration_team_agents_appendix(str(tmp_path)) == ""


def test_load_orchestration_team_agents_appendix_reads_agents_md(tmp_path) -> None:
    p = tmp_path / "AGENTS.md"
    p.write_text("# Team\n\nUse checklist A→B.\n", encoding="utf-8")
    out = load_orchestration_team_agents_appendix(str(tmp_path))
    assert "orchestration AGENTS.md" in out
    assert "checklist" in out


def test_load_orchestration_team_agents_appendix_prefers_uppercase(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("upper", encoding="utf-8")
    (tmp_path / "agents.md").write_text("lower", encoding="utf-8")
    out = load_orchestration_team_agents_appendix(str(tmp_path))
    assert "upper" in out
    assert "lower" not in out


def test_load_orchestration_team_agents_appendix_fallback_lowercase(tmp_path) -> None:
    (tmp_path / "agents.md").write_text("only lower", encoding="utf-8")
    out = load_orchestration_team_agents_appendix(str(tmp_path))
    assert "only lower" in out


def test_orchestration_state_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path))
    from mw4agent.config.paths import orchestration_state_dir

    d = orchestration_state_dir("abc-123")
    expect = os.path.normpath(os.path.join(str(tmp_path), "orchestrations", "abc-123"))
    assert os.path.normpath(d) == expect
