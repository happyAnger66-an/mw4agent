"""Import orchestration bundle → new orch id."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orbit.agents.agent_manager import AgentManager
from orbit.agents.events.stream import EventStream
from orbit.agents.types import AgentPayload, AgentRunMeta, AgentRunResult, AgentRunStatus
from orbit.config.paths import orchestration_state_dir, resolve_agent_workspace_dir
from orbit.gateway.orch_dump import build_orchestration_dump_zip
from orbit.gateway.orch_import import import_orchestration_bundle
from orbit.gateway.orchestrator import Orchestrator


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


def test_import_new_id_and_keeps_router_key_when_not_redacted(orch: Orchestrator) -> None:
    st = orch.create(session_key="sk", name="imp-test", participants=["main"])
    oid = st.orchId
    ws = Path(resolve_agent_workspace_dir("main"))
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "MEMORY.md").write_text("home", encoding="utf-8")

    orch_root = Path(orchestration_state_dir(oid))
    data = json.loads((orch_root / "orch.json").read_text(encoding="utf-8"))
    data["routerLlm"] = {"provider": "openai", "api_key": "secret-import-key"}
    (orch_root / "orch.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    raw, _fname = build_orchestration_dump_zip(
        orch_id=oid, orchestrator=orch, redact_secrets=False
    )
    st2 = import_orchestration_bundle(orch, raw)
    assert st2.orchId != oid
    data2 = json.loads(
        (Path(orchestration_state_dir(st2.orchId)) / "orch.json").read_text(encoding="utf-8")
    )
    assert data2["orchId"] == st2.orchId
    assert data2["routerLlm"]["api_key"] == "secret-import-key"
    assert data2.get("orchWorkspaceRoot") in (None, "")
    assert (data2.get("status") or "") == "idle"
