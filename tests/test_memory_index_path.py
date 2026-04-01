"""resolve_memory_index_db_path: per-workspace SQLite for LocalIndexBackend."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mw4agent.config.paths import (
    get_state_dir,
    resolve_agent_dir,
    resolve_agent_workspace_dir,
    resolve_memory_index_db_path,
    resolve_orchestration_agent_workspace_dir,
)


def test_default_workspace_uses_agent_memory_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("MW4AGENT_WORKSPACE_DIR", raising=False)
    aid = "main"
    ws = resolve_agent_workspace_dir(aid)
    db = resolve_memory_index_db_path(aid, ws)
    assert db == str(Path(resolve_agent_dir(aid)) / "memory" / "index.sqlite")


def test_orchestration_workspace_uses_orch_agent_memory_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("MW4AGENT_WORKSPACE_DIR", raising=False)
    orch_id = "o1"
    aid = "coder"
    ws = resolve_orchestration_agent_workspace_dir(orch_id, aid)
    db = resolve_memory_index_db_path(aid, ws)
    expected = (
        Path(get_state_dir())
        / "orchestrations"
        / orch_id
        / "agents"
        / aid
        / "memory"
        / "index.sqlite"
    )
    assert Path(db).resolve() == expected.resolve()


def test_custom_workspace_uses_hidden_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("MW4AGENT_WORKSPACE_DIR", raising=False)
    custom = os.path.join(str(tmp_path), "myproj", "ws")
    os.makedirs(custom, exist_ok=True)
    db = resolve_memory_index_db_path("main", custom)
    assert db == str(Path(custom) / ".mw4agent_memory" / "index.sqlite")
