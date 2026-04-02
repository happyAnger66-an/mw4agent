"""Orchestrator: reconcile persisted ``running`` after gateway restart."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mw4agent.agents.agent_manager import AgentManager
from mw4agent.agents.types import AgentPayload, AgentRunMeta, AgentRunResult, AgentRunStatus
from mw4agent.gateway.orchestrator import Orchestrator, _orch_state_path


class _FakeRunner:
    async def run(self, params):  # noqa: ANN001
        _ = params
        return AgentRunResult(
            payloads=[AgentPayload(text="ok")],
            meta=AgentRunMeta(duration_ms=0, status=AgentRunStatus.COMPLETED),
        )


@pytest.fixture()
def orch(tmp_path, monkeypatch) -> Orchestrator:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    (cfg_dir / "mw4agent.json").write_text(json.dumps({"llm": {"provider": "echo"}}), encoding="utf-8")
    return Orchestrator(agent_manager=AgentManager(), runner=_FakeRunner())


@pytest.mark.asyncio
async def test_reconcile_stale_running_marks_error_and_allows_send(orch: Orchestrator, tmp_path, monkeypatch) -> None:
    st = orch.create(session_key="sk", name="t", participants=["main"])
    oid = st.orchId
    path = Path(_orch_state_path(oid))
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "running"
    data["error"] = None
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    orch2 = Orchestrator(agent_manager=AgentManager(), runner=_FakeRunner())
    assert orch2.reconcile_stale_running_states() == 1

    loaded = orch2.get(oid)
    assert loaded is not None
    assert loaded.status == "error"
    assert loaded.error and "restarted" in loaded.error.lower()

    orch2.send(orch_id=oid, message="after reconcile")
    for _ in range(200):
        await asyncio.sleep(0.05)
        cur = orch2.get(oid)
        if cur and cur.status == "idle":
            break
    assert orch2.get(oid) is not None
    assert orch2.get(oid).status == "idle"
