"""AgentManager.delete and agent del CLI behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mw4agent.agents.agent_manager import AgentManager
from mw4agent.config.paths import DEFAULT_AGENT_ID, resolve_agent_dir


def test_delete_removes_agent_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    mgr = AgentManager()
    mgr.get_or_create("foo")
    agent_path = Path(resolve_agent_dir("foo"))
    assert agent_path.is_dir()
    assert (agent_path / "agent.json").exists()

    assert mgr.delete("foo") is True
    assert not agent_path.exists()


def test_delete_main_without_force_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    mgr = AgentManager()
    mgr.ensure_main()
    with pytest.raises(ValueError, match="main"):
        mgr.delete(DEFAULT_AGENT_ID, allow_main=False)


def test_delete_main_with_force(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    mgr = AgentManager()
    mgr.ensure_main()
    main_path = Path(resolve_agent_dir("main"))
    assert mgr.delete(DEFAULT_AGENT_ID, allow_main=True) is True
    assert not main_path.exists()


def test_delete_missing_returns_false(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    mgr = AgentManager()
    assert mgr.delete("nonexistent-agent-xyz") is False


def test_cli_agent_del_invocation(tmp_path, monkeypatch) -> None:
    """Smoke: click runner invokes del and removes directory."""
    import click
    from click.testing import CliRunner

    from mw4agent.cli.agent.register import register_agent_cli
    from mw4agent.cli.context import create_program_context

    def _build_cli() -> click.Group:
        @click.group()
        def cli() -> None:
            return None

        register_agent_cli(cli, create_program_context("0.0.0"))
        return cli

    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / ".mw4agent"))
    mgr = AgentManager()
    mgr.get_or_create("bar")
    assert Path(resolve_agent_dir("bar")).exists()

    r = CliRunner().invoke(_build_cli(), ["agent", "del", "bar"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out.get("ok") is True and out.get("agentId") == "bar"
    assert not Path(resolve_agent_dir("bar")).exists()
