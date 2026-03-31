"""Supervisor pipeline: A→B→C stroke + supervisor continue/stop (mock LLM)."""

from __future__ import annotations

import asyncio
import json
import types

import pytest

from mw4agent.agents.agent_manager import AgentManager
from mw4agent.agents.types import AgentPayload, AgentRunMeta, AgentRunResult, AgentRunStatus
from mw4agent.gateway.orchestrator import Orchestrator, _parse_supervisor_decision
from mw4agent.llm.backends import LLMUsage


class _FakeRunner:
    async def run(self, params):  # noqa: ANN001
        aid = getattr(params, "agent_id", None) or "?"
        return AgentRunResult(
            payloads=[AgentPayload(text=f"out-{aid}")],
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


def test_parse_supervisor_decision() -> None:
    d = _parse_supervisor_decision('{"action":"stop","reason":"ok"}')
    assert d["action"] == "stop"
    d2 = _parse_supervisor_decision(
        "```json\n"
        '{"action":"continue","reason":"x","brief_for_next_stroke":"do y"}\n'
        "```"
    )
    assert d2["action"] == "continue"
    assert "brief_for_next_stroke" in d2


@pytest.mark.asyncio
async def test_supervisor_pipeline_two_strokes_then_stop(orch: Orchestrator, monkeypatch) -> None:
    replies = iter(
        [
            '{"action":"continue","reason":"need more","brief_for_next_stroke":"expand section 1"}',
            '{"action":"stop","reason":"done","final_user_visible_summary":"Summary."}',
        ]
    )

    def fake_chat(*_a, **_kw):
        return next(replies), LLMUsage()

    monkeypatch.setattr("mw4agent.gateway.orchestrator._call_openai_chat", fake_chat)

    st = orch.create(
        session_key="sk",
        name="t",
        participants=["a1", "b1"],
        strategy="supervisor_pipeline",
        supervisor_pipeline=["a1", "b1"],
        supervisor_llm={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com",
            "api_key": "x",
        },
        supervisor_max_iterations=3,
    )
    orch.send(orch_id=st.orchId, message="hello")
    for _ in range(200):
        await asyncio.sleep(0.05)
        cur = orch.get(st.orchId)
        if cur and cur.status in ("idle", "error"):
            break
    final = orch.get(st.orchId)
    assert final is not None
    assert final.status == "idle"
    assert final.error is None
    assistants = [m for m in final.messages if m.role == "assistant" and m.speaker != "supervisor"]
    # 2 strokes × 2 agents = 4
    assert len(assistants) == 4
    sup_msgs = [m for m in final.messages if m.speaker == "supervisor"]
    assert len(sup_msgs) == 1
    assert "Summary" in sup_msgs[0].text


@pytest.mark.asyncio
async def test_supervisor_llm_retries_then_ok(orch: Orchestrator, monkeypatch) -> None:
    calls = {"n": 0}

    async def flaky_supervisor_llm(self, orch_id, sup, prompt):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("simulated network failure")
        return '{"action":"stop","reason":"ok"}'

    async def no_delay() -> None:
        return None

    monkeypatch.setattr(
        orch,
        "_supervisor_call_llm",
        types.MethodType(flaky_supervisor_llm, orch),
    )
    monkeypatch.setattr(
        "mw4agent.gateway.orchestrator._supervisor_retry_delay",
        no_delay,
    )

    st = orch.create(
        session_key="sk",
        name="t",
        participants=["main"],
        strategy="supervisor_pipeline",
        supervisor_pipeline=["main"],
        supervisor_llm={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com",
            "api_key": "x",
        },
        supervisor_llm_max_retries=5,
    )
    orch.send(orch_id=st.orchId, message="hello")
    for _ in range(120):
        await asyncio.sleep(0.02)
        cur = orch.get(st.orchId)
        if cur and cur.status in ("idle", "error"):
            break
    final = orch.get(st.orchId)
    assert final is not None
    assert final.status == "idle"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_supervisor_missing_llm_errors(orch: Orchestrator) -> None:
    st = orch.create(
        session_key="sk",
        name="t",
        participants=["main"],
        strategy="supervisor_pipeline",
        supervisor_pipeline=["main"],
        supervisor_llm=None,
    )
    orch.send(orch_id=st.orchId, message="hi")
    for _ in range(80):
        await asyncio.sleep(0.05)
        cur = orch.get(st.orchId)
        if cur and cur.status in ("idle", "error"):
            break
    final = orch.get(st.orchId)
    assert final is not None
    assert final.status == "error"
    assert final.error and "supervisorLlm" in final.error
