"""Gateway RPC agents.list for dashboard multi-agent view."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient


@pytest.fixture()
def gateway_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    (cfg_dir / "mw4agent.json").write_text(json.dumps({"llm": {"provider": "echo"}}), encoding="utf-8")

    from mw4agent.gateway.server import create_app

    app = create_app(session_file="")
    with TestClient(app) as client:
        yield client


def test_agents_list_returns_main_and_paths(gateway_client: TestClient) -> None:
    res = gateway_client.post("/rpc", json={"id": "t1", "method": "agents.list", "params": {}})
    assert res.status_code == 200
    body = res.json()
    assert body.get("ok") is True
    payload = body.get("payload") or {}
    agents = payload.get("agents")
    assert isinstance(agents, list)
    assert agents
    main = next((a for a in agents if a.get("agentId") == "main"), None)
    assert main is not None
    assert main.get("agentDir")
    assert main.get("workspaceDir")
    assert main.get("sessionsFile")
    rs = main.get("runStatus") or {}
    assert rs.get("state") in ("idle", "running")
    assert "activeRuns" in rs
