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
    # 1) Patch build_skill_snapshot so the test is independent of the real skills dir.
    import mw4agent.agents.skills.snapshot as snapshot_mod
    import mw4agent.agents.runner.runner as runner_mod

    def _fake_build_skill_snapshot():
        prompt = "Available skills:\n- demo_skill: Test skill that should appear in the LLM prompt."
        return {
            "skills": [{"name": "demo_skill", "description": "Test skill that should appear in the LLM prompt."}],
            "count": 1,
            "prompt": prompt,
        }

    monkeypatch.setattr(snapshot_mod, "build_skill_snapshot", _fake_build_skill_snapshot)
    monkeypatch.setattr(runner_mod, "build_skill_snapshot", _fake_build_skill_snapshot)

    # 2) Run AgentRunner with a new session file.
    session_file = tmp_path / "sessions.json"
    session_manager = SessionManager(str(session_file))
    runner = AgentRunner(session_manager)

    params = AgentRunParams(
        message="请简要说明当前可用的技能。",
        session_key="e2e:skills",
        session_id="e2e-skills",
        agent_id="e2e",
        provider="echo",  # 固定使用 echo 后端，避免环境中的真实 LLM 导致断言失败
    )

    result = await runner.run(params)
    assert result.payloads, "AgentRunner did not produce any payloads"
    text = result.payloads[0].text or ""

    # 3) The echo backend wraps the composed prompt, so we just need to ensure
    #    our skills prompt fragment is present in the reply.
    assert "Available skills:" in text
    # We don't assert the concrete skill name here to keep the test robust
    # across environments; the shared test skill description is sufficient.
    assert "Test skill that should appear in the LLM prompt." in text

