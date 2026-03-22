"""agent_id 绑定时 session 存储与 memory/workspace 目录应对齐到 agents/<id>/。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mw4agent.agents.agent_manager import AgentManager
from mw4agent.agents.runner.runner import AgentRunner, _resolve_run_workspace_dir
from mw4agent.agents.session.multi_manager import MultiAgentSessionManager
from mw4agent.agents.tools.memory_tool import MemorySearchTool
from mw4agent.agents.tools.registry import ToolRegistry
from mw4agent.agents.types import AgentRunParams
from mw4agent.config.paths import resolve_agent_sessions_file, resolve_agent_workspace_dir
from mw4agent.llm.backends import LLMUsage


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def test_resolve_run_workspace_dir_per_agent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / "mw"))
    p = AgentRunParams(message="m", agent_id="coders")
    got = _resolve_run_workspace_dir(p)
    assert _norm(got).endswith("agents/coders/workspace")

    p_main = AgentRunParams(message="m", agent_id=None)
    assert _norm(_resolve_run_workspace_dir(p_main)).endswith("agents/main/workspace")

    p_explicit = AgentRunParams(message="m", agent_id="coders", workspace_dir=str(tmp_path / "custom"))
    assert _resolve_run_workspace_dir(p_explicit) == str((tmp_path / "custom").resolve())


def test_multi_agent_session_store_and_transcript_under_agent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / "mw"))
    aid = "coders"
    sess_file = resolve_agent_sessions_file(aid)
    assert "agents" in _norm(sess_file)
    assert f"agents/{aid}/sessions/sessions.json" in _norm(sess_file)

    mgr = MultiAgentSessionManager(AgentManager())
    tp = mgr.resolve_transcript_path("sid-42", agent_id=aid)
    assert f"agents/{aid}/sessions" in _norm(tp)
    assert tp.endswith("sid-42.jsonl")


@pytest.mark.asyncio
async def test_runner_with_agent_id_writes_transcript_cwd_to_agent_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    """模拟 Feishu 绑定 agent_id 后直连 Runner：transcript 在 agents/<id>/sessions，cwd 为 agents/<id>/workspace。"""
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / "mw"))
    aid = "coders"
    ws = resolve_agent_workspace_dir(aid)
    Path(ws).mkdir(parents=True, exist_ok=True)

    mgr = MultiAgentSessionManager(AgentManager())
    runner = AgentRunner(mgr)
    runner.tool_registry = ToolRegistry()

    def fake_gen(params, messages=None):
        return ("ok", "echo", "m", LLMUsage())

    monkeypatch.setattr("mw4agent.agents.runner.runner.generate_reply", fake_gen)

    params = AgentRunParams(
        message="hi",
        session_id="sess-feishu-1",
        session_key="feishu:coders:oc_xxx",
        agent_id=aid,
        deliver=False,
        channel="feishu:coders",
    )
    await runner.run(params)

    tp = mgr.resolve_transcript_path("sess-feishu-1", agent_id=aid)
    assert f"agents/{aid}/sessions" in _norm(tp)
    assert Path(tp).is_file()
    first_line = Path(tp).read_text(encoding="utf-8").splitlines()[0]
    hdr = json.loads(first_line)
    assert hdr.get("type") == "session"
    assert Path(hdr["cwd"]).resolve() == Path(ws).resolve()


@pytest.mark.asyncio
async def test_memory_search_uses_runner_style_workspace_for_bound_agent(
    monkeypatch, tmp_path: Path
) -> None:
    """memory_search 在 context.workspace_dir 为 per-agent workspace 时从该目录读 MEMORY.md。"""
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / "mw"))
    aid = "coders"
    ws = resolve_agent_workspace_dir(aid)
    Path(ws).mkdir(parents=True, exist_ok=True)
    (Path(ws) / "MEMORY.md").write_text("# Notes\nproject alpha unique_token_xyz\n", encoding="utf-8")

    mgr = MultiAgentSessionManager(AgentManager())
    runner = AgentRunner(mgr)
    reg = ToolRegistry()
    reg.register(MemorySearchTool())
    runner.tool_registry = reg

    params = AgentRunParams(
        message="x",
        agent_id=aid,
        channel="feishu:coders",
    )
    ctx = {
        "workspace_dir": _resolve_run_workspace_dir(params),
        "agent_id": aid,
    }
    res = await runner.execute_tool(
        "tc-mem",
        "memory_search",
        {"query": "unique_token_xyz"},
        context=ctx,
    )
    assert res.success is True
    payload = res.result
    results = payload.get("results") or []
    assert results, payload
    texts = [str(r.get("snippet") or r.get("text") or "") for r in results]
    assert any("unique_token_xyz" in t for t in texts)
