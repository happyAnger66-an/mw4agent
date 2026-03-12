"""E2E: AgentRunner attaches SkillSnapshot to session and LLM sees skills in prompt."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mw4agent.agents.runner.runner import AgentRunner
from mw4agent.agents.session.manager import SessionManager
from mw4agent.agents.types import AgentRunParams


@pytest.mark.asyncio
async def test_agent_skills_snapshot_is_used_in_llm_prompt(tmp_path: Path, monkeypatch) -> None:
    """Create a test skill, run AgentRunner, and assert LLM reply contains skill info.

    Flow:
      1. Point MW4AGENT_SKILLS_DIR to a temp dir and create an encrypted (or plaintext fallback)
         skill file via SkillManager.
      2. Run AgentRunner with a fresh SessionManager.
      3. Verify the echo LLM reply includes the skills summary emitted by build_skill_snapshot().
    """
    # 1) Point skills manager to the shared test skills directory.
    repo_root = Path(__file__).resolve().parents[2]
    skills_dir = repo_root / "tests" / "data" / "skills"
    monkeypatch.setenv("MW4AGENT_SKILLS_DIR", str(skills_dir))

    # Reset the default SkillManager singleton so it picks up MW4AGENT_SKILLS_DIR.
    import mw4agent.skills.manager as skills_mod

    skills_mod._default_skill_manager = None  # type: ignore[attr-defined]

    # 2) Run AgentRunner with a new session file.
    session_file = tmp_path / "sessions.json"
    session_manager = SessionManager(str(session_file))
    runner = AgentRunner(session_manager)

    params = AgentRunParams(
        message="请简要说明当前可用的技能。",
        session_key="e2e:skills",
        session_id="e2e-skills",
        agent_id="e2e",
    )

    result = await runner.run(params)
    assert result.payloads, "AgentRunner did not produce any payloads"
    text = result.payloads[0].text or ""

    # 3) The echo backend wraps the composed prompt, so we just need to ensure
    #    our skills prompt fragment is present in the reply.
    assert "Available skills:" in text
    assert "demo_skill" in text
    assert "Test skill that should appear in the LLM prompt." in text

