"""orchestrate.dump ZIP layout (orch.json, team md, per-agent workspaces, tools_skills.json)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from orbit.agents.agent_manager import AgentManager
from orbit.agents.events.stream import EventStream
from orbit.agents.types import AgentPayload, AgentRunMeta, AgentRunResult, AgentRunStatus
from orbit.config.paths import orchestration_state_dir, resolve_agent_workspace_dir, resolve_orchestration_agent_workspace_dir
from orbit.gateway.orch_dump import build_orchestration_dump_zip
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


def test_dump_zip_includes_orchestration_and_agent_paths(orch: Orchestrator) -> None:
    st = orch.create(session_key="sk", name="my-orch", participants=["main", "a2"])
    oid = st.orchId

    for aid in ("main", "a2"):
        ws = Path(resolve_agent_workspace_dir(aid))
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "MEMORY.md").write_text(f"home-{aid}", encoding="utf-8")

    root = Path(orchestration_state_dir(oid))
    (root / "AGENTS.md").write_text("team-agents", encoding="utf-8")

    ow = Path(resolve_orchestration_agent_workspace_dir(oid, "main"))
    ow.mkdir(parents=True, exist_ok=True)
    (ow / "MEMORY.md").write_text("orch-ws-main", encoding="utf-8")
    sub = ow / "notes"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "extra.md").write_text("nested", encoding="utf-8")

    raw, fname = build_orchestration_dump_zip(orch_id=oid, orchestrator=orch)
    assert fname.endswith(".zip")
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    top = names[0].split("/")[0]
    assert top.startswith("orch-dump_")
    assert any(n.endswith("/orchestration/orch.json") for n in names)
    assert any(n.endswith("/orchestration/AGENTS.md") for n in names)
    assert any(
        "/agents/main/home_workspace/MEMORY.md" in n and n.startswith(top) for n in names
    )
    assert any("/agents/main/orchestration_workspace/MEMORY.md" in n for n in names)
    assert any("/agents/main/orchestration_workspace/notes/extra.md" in n for n in names)
    data = json.loads(zf.read(next(n for n in names if n.endswith("/agents/main/tools_skills.json"))))
    assert data["agentId"] == "main"
    assert isinstance(data["tools"], list)
    man = json.loads(zf.read(next(n for n in names if n.endswith("/manifest.json"))))
    assert man.get("formatVersion") == 1
    assert man.get("sourceOrchId") == oid
    assert man.get("secretsRedacted") is True
