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

    # Point SkillManager to the shared test skills directory and reset singleton cache.
    repo_root = Path(__file__).resolve().parents[2]
    skills_dir = repo_root / "tests" / "data" / "skills"
    monkeypatch.setenv("MW4AGENT_SKILLS_DIR", str(skills_dir))
    global _default_skill_manager
    _default_skill_manager = None

    # Use a temporary session file.
    session_file = tmp_path / "sessions.json"
    session_manager = SessionManager(str(session_file))

    runner = AgentRunner(session_manager)

    params = AgentRunParams(
        message="Hello with skills",
        session_key="e2e:skills",
        session_id="e2e-skills",
        agent_id="test-agent",
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
    assert snapshot["skills"][0]["name"] == "demo_skill"

    # Echo backend sees the composed message, which includes the skills prompt.
    assert result.payloads, "No payloads returned from AgentRunner"
    text = result.payloads[0].text
    # The reply should include skill description, proving the skill snapshot was digested.
    assert "Test skill that should appear in the LLM prompt." in text

