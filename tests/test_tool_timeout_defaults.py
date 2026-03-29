from __future__ import annotations

from pathlib import Path

import pytest

from mw4agent.agents.tools.exec_tool import ExecTool
from mw4agent.agents.tools.timeout_defaults import (
    resolve_default_tool_timeout_ms,
    resolve_timeout_ms_param,
)
from mw4agent.config.root import write_root_config


def test_resolve_default_tool_timeout_ms_from_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("MW4AGENT_TOOLS_TIMEOUT_MS", raising=False)
    assert resolve_default_tool_timeout_ms() is None

    write_root_config({"tools": {"timeout_ms": 45000}})
    assert resolve_default_tool_timeout_ms() == 45000


def test_env_overrides_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(tmp_path))
    write_root_config({"tools": {"timeout_ms": 1000}})
    monkeypatch.setenv("MW4AGENT_TOOLS_TIMEOUT_MS", "99999")
    assert resolve_default_tool_timeout_ms() == 99999


def test_resolve_timeout_ms_param_prefers_explicit() -> None:
    assert (
        resolve_timeout_ms_param(
            {"timeout_ms": 5000},
            {"default_tool_timeout_ms": 99_000},
            param_key="timeout_ms",
            default_ms=10_000,
            min_ms=100,
            max_ms=120_000,
        )
        == 5000
    )


def test_resolve_timeout_ms_param_uses_context_when_missing() -> None:
    assert (
        resolve_timeout_ms_param(
            {},
            {"default_tool_timeout_ms": 60_000},
            param_key="timeout_ms",
            default_ms=10_000,
            min_ms=100,
            max_ms=120_000,
        )
        == 60_000
    )


@pytest.mark.asyncio
async def test_exec_uses_context_default_timeout(tmp_path: Path) -> None:
    tool = ExecTool()
    result = await tool.execute(
        "t1",
        {"command": "sleep 2"},
        context={"workspace_dir": str(tmp_path), "default_tool_timeout_ms": 200},
    )
    assert result.success is False
    assert "timed out" in (result.error or "").lower() or result.result.get("timed_out") is True
