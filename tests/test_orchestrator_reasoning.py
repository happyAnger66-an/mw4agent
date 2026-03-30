"""Orchestrator：send 时 reasoning_level 写入 orchReasoningLevel（与前端 orchestrate.send 对齐）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from mw4agent.agents.agent_manager import AgentManager
from mw4agent.agents.types import AgentPayload, AgentRunMeta, AgentRunResult, AgentRunStatus
from mw4agent.gateway.orchestrator import Orchestrator


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
async def test_send_with_reasoning_level_persists_orch_reasoning_level(orch: Orchestrator) -> None:
    st = orch.create(session_key="sk", name="t", participants=["main"])
    oid = st.orchId
    assert getattr(st, "orchReasoningLevel", None) in (None,)

    orch.send(orch_id=oid, message="hi", reasoning_level="stream")
    loaded = orch.get(oid)
    assert loaded is not None
    assert loaded.orchReasoningLevel == "stream"


@pytest.mark.asyncio
async def test_send_without_reasoning_level_does_not_clear_previous(orch: Orchestrator) -> None:
    st = orch.create(session_key="sk", name="t", participants=["main"])
    oid = st.orchId
    orch.send(orch_id=oid, message="a", reasoning_level="stream")
    for _ in range(100):
        await asyncio.sleep(0.05)
        cur = orch.get(oid)
        if cur and cur.status == "idle":
            break
    orch.send(orch_id=oid, message="b")
    loaded = orch.get(oid)
    assert loaded is not None
    assert loaded.orchReasoningLevel == "stream"
