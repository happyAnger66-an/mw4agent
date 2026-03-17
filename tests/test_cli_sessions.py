from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mw4agent.cli.main import main as cli_main
from mw4agent.agents.session import MultiAgentSessionManager
from mw4agent.agents.agent_manager import AgentManager


def _run_cli(argv: list[str]) -> str:
    try:
        cli_main(argv)
    except SystemExit as e:
        if e.code != 0:
            raise
    return ""


def test_sessions_sessions_lists_agent_sessions(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    # Avoid encryption warning noise (must be base64-encoded 32 bytes).
    monkeypatch.setenv("MW4AGENT_SECRET_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

    mgr = MultiAgentSessionManager(agent_manager=AgentManager())
    # create 2 sessions under agent "main"
    s1 = mgr.get_or_create_session(session_id="s1", session_key="k1", agent_id="main")
    mgr.update_session(s1.session_id, agent_id="main", message_count=3)
    s2 = mgr.get_or_create_session(session_id="s2", session_key="k2", agent_id="main")
    mgr.update_session(s2.session_id, agent_id="main", message_count=7)

    _run_cli(["mw4agent", "sessions", "--agent", "main"])
    out = capsys.readouterr().out
    assert "Sessions (agent=main)" in out
    assert "s1" in out and "s2" in out


def test_sessions_sessions_json_output(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    monkeypatch.setenv("MW4AGENT_SECRET_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

    mgr = MultiAgentSessionManager(agent_manager=AgentManager())
    mgr.get_or_create_session(session_id="s1", session_key="k1", agent_id="main")

    _run_cli(["mw4agent", "sessions", "--agent", "main", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data.get("sessions"), list)
    assert any(s.get("sessionId") == "s1" for s in data["sessions"])

