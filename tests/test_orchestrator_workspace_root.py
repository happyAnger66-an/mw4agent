"""Shared orchestration workspace root (``orchWorkspaceRoot``) persistence and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orbit.agents.agent_manager import AgentManager
from orbit.agents.events.stream import EventStream
from orbit.agents.types import AgentPayload, AgentRunMeta, AgentRunResult, AgentRunStatus
from orbit.gateway.orchestrator import (
    Orchestrator,
    _normalize_orch_workspace_root,
    _orch_state_path,
)


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


def test_normalize_orch_workspace_root_requires_existing_dir(tmp_path) -> None:
    d = tmp_path / "w"
    d.mkdir()
    assert _normalize_orch_workspace_root(str(d)) == str(d.resolve())
    with pytest.raises(ValueError, match="does not exist"):
        _normalize_orch_workspace_root(str(tmp_path / "nope"))


def test_create_with_orch_workspace_root(orch: Orchestrator, tmp_path) -> None:
    d = tmp_path / "shared"
    d.mkdir()
    st = orch.create(
        session_key="sk",
        name="t",
        participants=["main"],
        orch_workspace_root=str(d),
    )
    assert st.orchWorkspaceRoot == str(d.resolve())
    raw = json.loads(Path(_orch_state_path(st.orchId)).read_text(encoding="utf-8"))
    assert raw.get("orchWorkspaceRoot") == str(d.resolve())


def test_set_workspace_root_and_clear(orch: Orchestrator, tmp_path) -> None:
    d = tmp_path / "proj"
    d.mkdir()
    st = orch.create(session_key="sk", name="t", participants=["main"])
    oid = st.orchId
    out = orch.set_workspace_root(oid, workspace_root=str(d))
    assert out.orchWorkspaceRoot == str(d.resolve())

    cleared = orch.set_workspace_root(oid, workspace_root="")
    assert cleared.orchWorkspaceRoot is None

    again = orch.get(oid)
    assert again is not None
    assert again.orchWorkspaceRoot is None


def test_set_workspace_root_refuses_while_running(orch: Orchestrator, tmp_path) -> None:
    d = tmp_path / "proj"
    d.mkdir()
    st = orch.create(session_key="sk", name="t", participants=["main"])
    oid = st.orchId
    cur = orch.get(oid)
    assert cur is not None
    cur.status = "running"
    orch._save(cur)
    with pytest.raises(ValueError, match="running"):
        orch.set_workspace_root(oid, workspace_root=str(d))
