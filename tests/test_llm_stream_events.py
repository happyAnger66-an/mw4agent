from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mw4agent.agents.runner.runner import AgentRunner
from mw4agent.agents.session.manager import SessionManager
from mw4agent.agents.types import AgentRunParams, StreamEvent
from mw4agent.agents.tools.base import ToolResult
from mw4agent.channels import feishu_llm_stream as feishu_llm_mod
from mw4agent.channels.feishu_llm_stream import (
    FEISHU_LLM_STREAM_META_KEY,
    format_llm_stream_event_for_feishu,
    feishu_session_effective_llm_stream,
    parse_feishu_thinking_command,
)
from mw4agent.llm import LLMUsage


def test_parse_feishu_thinking_command() -> None:
    assert parse_feishu_thinking_command("/thinking") == (True, "")
    assert parse_feishu_thinking_command("  /thinking  ") == (True, "")
    assert parse_feishu_thinking_command("/thinking do x") == (True, "do x")
    assert parse_feishu_thinking_command("/close_thinking") == (False, "")
    assert parse_feishu_thinking_command("/close_thinking y") == (False, "y")
    assert parse_feishu_thinking_command("hello") == (None, "hello")


def test_feishu_session_effective_llm_stream(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(feishu_llm_mod, "feishu_llm_stream_enabled", lambda: True)
    sm = SessionManager(str(tmp_path / "sessions.json"))
    sm.get_or_create_session("sid", "sk", "main")
    assert feishu_session_effective_llm_stream(sm, "sid") is True
    sm.update_session("sid", metadata={FEISHU_LLM_STREAM_META_KEY: False})
    assert feishu_session_effective_llm_stream(sm, "sid") is False
    sm.update_session("sid", metadata={FEISHU_LLM_STREAM_META_KEY: True})
    assert feishu_session_effective_llm_stream(sm, "sid") is True

    monkeypatch.setattr(feishu_llm_mod, "feishu_llm_stream_enabled", lambda: False)
    assert feishu_session_effective_llm_stream(sm, "sid") is False


def test_format_llm_stream_event_includes_thinking() -> None:
    ev = StreamEvent(
        stream="llm",
        type="message",
        data={
            "phase": "tool_loop",
            "round": 0,
            "thinking": "step 1",
            "tool_calls": [{"name": "read", "arguments_preview": "{}"}],
        },
    )
    t = format_llm_stream_event_for_feishu(ev)
    assert t is not None
    assert "思考" in t
    assert "read" in t


@pytest.mark.asyncio
async def test_runner_emits_llm_stream_on_tool_loop_round(tmp_path: Path, monkeypatch) -> None:
    import mw4agent.agents.runner.runner as runner_mod

    monkeypatch.setattr(runner_mod, "MAX_TOOL_ROUNDS", 2)
    calls: dict[str, int] = {"n": 0}

    def fake_with_tools(params, messages, tool_defs):
        calls["n"] += 1
        if calls["n"] == 1:
            return (
                "<think>inner</think>visible",
                [{"id": "1", "name": "read", "arguments": {"path": "x"}}],
                "echo",
                "m",
                LLMUsage(),
            )
        return ("final reply", [], "echo", "m", LLMUsage())

    monkeypatch.setattr(runner_mod, "generate_reply_with_tools", fake_with_tools)

    sm = SessionManager(str(tmp_path / "sessions.json"))
    runner = AgentRunner(sm)
    runner.execute_tool = AsyncMock(return_value=ToolResult(success=True, result={"ok": True}))

    await runner.run(
        AgentRunParams(
            message="hi",
            session_id="s1",
            session_key="k1",
            agent_id="main",
            provider="echo",
        )
    )
    llm_events = [e for e in runner.event_stream.get_events() if e.stream == "llm" and e.type == "message"]
    assert llm_events, "expected llm stream events"
    first_loop = next(
        (e for e in llm_events if (e.data or {}).get("phase") == "tool_loop" and (e.data or {}).get("round") == 0),
        None,
    )
    assert first_loop is not None
    data = first_loop.data
    assert data.get("thinking") == "inner"
    assert data.get("phase") == "tool_loop"
    assert data.get("round") == 0


@pytest.mark.asyncio
async def test_runner_single_turn_strips_thinking_from_payload(tmp_path: Path, monkeypatch) -> None:
    import mw4agent.agents.runner.runner as runner_mod

    monkeypatch.setattr(
        runner_mod,
        "generate_reply",
        lambda params, messages=None: ("<think>plan</think>visible text", "echo", "m", LLMUsage()),
    )

    sm = SessionManager(str(tmp_path / "sessions.json"))
    runner = AgentRunner(sm)
    monkeypatch.setattr(runner.tool_registry, "list_tools", lambda: [])

    result = await runner.run(
        AgentRunParams(
            message="hi",
            session_id="s2",
            session_key="k2",
            agent_id="main",
            provider="echo",
        )
    )
    assert result.payloads[0].text == "visible text"
    llm_events = [e for e in runner.event_stream.get_events() if e.stream == "llm" and e.type == "message"]
    single = next((e for e in llm_events if (e.data or {}).get("phase") == "single_turn"), None)
    assert single is not None
    assert (single.data or {}).get("thinking") == "plan"
