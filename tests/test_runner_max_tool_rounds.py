from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mw4agent.agents.runner.runner import AgentRunner
from mw4agent.agents.session.manager import SessionManager
from mw4agent.agents.types import AgentRunParams
from mw4agent.agents.tools.base import ToolResult
from mw4agent.llm.backends import LLMUsage


@pytest.mark.asyncio
async def test_tool_loop_max_rounds_forces_finalize_and_stop_reason(tmp_path: Path, monkeypatch) -> None:
    import mw4agent.agents.runner.runner as runner_mod

    monkeypatch.setattr(runner_mod, "MAX_TOOL_ROUNDS", 2)

    calls = {"n": 0}

    def fake_with_tools(params, messages, tool_defs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return (
                "",
                [{"id": "1", "name": "read", "arguments": {"path": "x"}}],
                "echo",
                "m",
                LLMUsage(),
            )
        return ("unexpected", [], "echo", "m", LLMUsage())

    def fake_generate_reply(params, messages=None):
        return ("[finalize] summary.", "echo", "m", LLMUsage())

    monkeypatch.setattr(runner_mod, "generate_reply_with_tools", fake_with_tools)
    monkeypatch.setattr(runner_mod, "generate_reply", fake_generate_reply)

    sm = SessionManager(str(tmp_path / "sessions.json"))
    runner = AgentRunner(sm)
    runner.execute_tool = AsyncMock(return_value=ToolResult(success=True, result={"ok": True}))

    res = await runner.run(
        AgentRunParams(
            message="hello",
            session_id="sid1",
            session_key="key1",
            agent_id="main",
            provider="echo",
        )
    )
    assert res.meta.stop_reason == "max_tool_rounds"
    assert res.payloads and "[finalize]" in (res.payloads[0].text or "")
    assert runner.execute_tool.await_count == 2
