"""Gateway RPC ``orchestrate.trace.list`` 最小用例。"""

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


def test_orchestrate_trace_list_requires_orch_id(gateway_client: TestClient) -> None:
    res = gateway_client.post(
        "/rpc",
        json={"id": "t-trace-1", "method": "orchestrate.trace.list", "params": {}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("ok") is False
    assert (body.get("error") or {}).get("code") == "invalid_request"


def test_orchestrate_trace_list_ok_empty_when_no_trace_file(gateway_client: TestClient) -> None:
    res = gateway_client.post(
        "/rpc",
        json={
            "id": "t-trace-2",
            "method": "orchestrate.trace.list",
            "params": {"orchId": "00000000-0000-4000-8000-000000000001", "afterSeq": 0, "limit": 10},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("ok") is True
    payload = body.get("payload") or {}
    assert payload.get("events") == []
