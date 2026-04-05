"""Orchestrator.inspect_participants_capabilities: per-agent tools + skills audit payload."""

from __future__ import annotations

import json

import pytest

from orbit.agents.agent_manager import AgentManager
from orbit.agents.events.stream import EventStream
from orbit.agents.types import AgentPayload, AgentRunMeta, AgentRunResult, AgentRunStatus
from orbit.gateway.orchestrator import Orchestrator, collect_inspect_agent_ids


class _FakeRunner:
    def __init__(self) -> None:
        self.event_stream = EventStream()

    async def run(self, params):  # noqa: ANN001
        _ = params
        return AgentRunResult(
            payloads=[AgentPayload(text="ok")],
            meta=AgentRunMeta(duration_ms=0, status=AgentRunStatus.COMPLETED),
        )


@pytest.fixture()
def orch(tmp_path, monkeypatch) -> Orchestrator:
    monkeypatch.setenv("ORBIT_STATE_DIR", str(tmp_path / ".orbit"))
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORBIT_CONFIG_DIR", str(cfg_dir))
    (cfg_dir / "orbit.json").write_text(json.dumps({"llm": {"provider": "echo"}}), encoding="utf-8")
    return Orchestrator(agent_manager=AgentManager(), runner=_FakeRunner())


def test_collect_inspect_agent_ids_dag_and_supervisor(orch: Orchestrator) -> None:
    st = orch.create(
        session_key="sk",
        name="t",
        participants=["main"],
        strategy="dag",
        dag={
            "nodes": [
                {"id": "a", "agentId": "worker", "title": "A", "dependsOn": []},
                {"id": "b", "agentId": "main", "title": "B", "dependsOn": ["a"]},
            ],
            "parallelism": 2,
        },
    )
    st2 = orch.get(st.orchId)
    assert st2 is not None
    st2.supervisorPipeline = ["reviewer"]
    orch._save(st2)  # noqa: SLF001

    loaded = orch.get(st.orchId)
    assert loaded is not None
    ids = collect_inspect_agent_ids(loaded)
    assert ids == ["main", "worker", "reviewer"]


def test_inspect_participants_capabilities_shape(orch: Orchestrator) -> None:
    st = orch.create(session_key="sk", name="t", participants=["main", "a2"])
    payload = orch.inspect_participants_capabilities(st.orchId)
    assert payload["orchId"] == st.orchId
    agents = payload["agents"]
    assert len(agents) == 2
    for row in agents:
        assert "agentId" in row
        assert isinstance(row["tools"], list)
        assert all(isinstance(x, str) for x in row["tools"])
        assert isinstance(row["skills"], list)
        assert "skillsCount" in row
        assert "skillsPromptCount" in row
