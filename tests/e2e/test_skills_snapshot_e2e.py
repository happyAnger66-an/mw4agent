from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from mw4agent.agents.runner.runner import AgentRunner
from mw4agent.agents.session.manager import SessionManager
from mw4agent.agents.types import AgentRunParams
from mw4agent.skills.manager import _default_skill_manager


@pytest.mark.asyncio
async def test_skills_snapshot_attached_and_used(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: skill snapshot is attached to session and consumed by LLM."""

    # Patch build_skill_snapshot so the test is independent of the real skills dir.
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

    # Use a temporary session file.
    session_file = tmp_path / "sessions.json"
    session_manager = SessionManager(str(session_file))

    runner = AgentRunner(session_manager)

    params = AgentRunParams(
        message="Hello with skills",
        session_key="e2e:skills",
        session_id="e2e-skills",
        agent_id="test-agent",
        provider="echo",  # 固定使用 echo 后端，避免环境中的真实 LLM 导致断言失败
    )

    result = await runner.run(params)

    # Session should have been created and contain skills_snapshot in metadata.
    sessions = session_manager.list_sessions()
    assert sessions, "No sessions created"
    entry = sessions[0]
    assert entry.metadata is not None
    assert "skills_snapshot" in entry.metadata
    snapshot = entry.metadata["skills_snapshot"]
    assert snapshot["count"] == 1

    # Echo backend sees the composed message, which includes the skills prompt.
    assert result.payloads, "No payloads returned from AgentRunner"
    text = result.payloads[0].text
    # The reply should include skill description, proving the skill snapshot was digested.
    assert "Test skill that should appear in the LLM prompt." in text

