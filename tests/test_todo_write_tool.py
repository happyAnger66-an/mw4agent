"""Tests for todo_write tool, disk store, and system snapshot."""

from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from orbit.agents.tools.base import AgentTool, ToolResult
from orbit.agents.tools.todo_store import (
    append_todos_snapshot_to_prompt,
    format_todos_snapshot_for_system,
    read_todos_file,
    resolve_todos_storage_path,
    write_todos_file,
)
from orbit.agents.tools.todo_write_prompt import TODO_WRITE_TOOL_USAGE_FOR_DESCRIPTION
from orbit.agents.tools.todo_write_tool import TodoWriteTool


class _Named(AgentTool):
    def __init__(self, name: str) -> None:
        super().__init__(name=name, description="x", parameters={}, owner_only=False)

    async def execute(self, tool_call_id, params, context=None):
        return ToolResult(success=True, result={})


@pytest.mark.asyncio
async def test_todo_write_persists_to_disk(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)
    tool = TodoWriteTool()
    ctx = {
        "session_key": "feishu:u1",
        "session_id": "sess-1",
        "agent_workspace_dir": ws,
    }
    r1 = await tool.execute(
        "1",
        {
            "todos": [
                {
                    "content": "Step A",
                    "status": "pending",
                    "active_form": "Doing step A",
                }
            ],
        },
        ctx,
    )
    assert r1.success
    path = r1.result.get("storagePath")
    assert path and os.path.isfile(path)
    assert read_todos_file(path) == r1.result["newTodos"]

    r2 = await tool.execute(
        "2",
        {
            "todos": [
                {
                    "content": "Step A",
                    "status": "completed",
                    "active_form": "Doing step A",
                },
                {
                    "content": "Step B",
                    "status": "in_progress",
                    "active_form": "Doing step B",
                },
            ],
        },
        ctx,
    )
    assert r2.success
    assert len(r2.result["oldTodos"]) == 1
    on_disk = read_todos_file(str(path))
    assert len(on_disk) == 2


@pytest.mark.asyncio
async def test_todo_write_orch_shared_path(monkeypatch, tmp_path):
    orch_root = tmp_path / "orchs" / "my-orch"
    orch_root.mkdir(parents=True)

    def fake_orch_state_dir(oid: str) -> str:
        assert oid == "my-orch"
        return str(orch_root)

    monkeypatch.setattr(
        "orbit.agents.tools.todo_store.orchestration_state_dir",
        fake_orch_state_dir,
    )
    tool = TodoWriteTool()
    ctx = {
        "session_key": "orch:my-orch",
        "session_id": "per-agent-session-xyz",
        "agent_workspace_dir": "/unused/for-orch",
    }
    r = await tool.execute(
        "1",
        {
            "todos": [
                {
                    "content": "Shared",
                    "status": "in_progress",
                    "active_form": "Working on shared",
                },
            ],
        },
        ctx,
    )
    assert r.success
    p = r.result["storagePath"]
    assert p.endswith("shared_todos.json")
    assert os.path.isfile(p)


@pytest.mark.asyncio
async def test_todo_write_all_completed_clears_file(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)
    tool = TodoWriteTool()
    ctx = {"session_key": "k", "session_id": "s", "agent_workspace_dir": ws}
    await tool.execute(
        "1",
        {
            "todos": [
                {"content": "A", "status": "completed", "active_form": "Finishing A"},
            ],
        },
        ctx,
    )
    path = resolve_todos_storage_path(session_key="k", session_id="s", agent_workspace_dir=ws)
    assert path
    assert read_todos_file(path) == []


@pytest.mark.asyncio
async def test_todo_write_accepts_active_form_camel_case(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)
    tool = TodoWriteTool()
    r = await tool.execute(
        "1",
        {
            "todos": [
                {
                    "content": "x",
                    "status": "pending",
                    "activeForm": "Working on x",
                },
            ],
        },
        {"session_key": "k", "session_id": "s", "agent_workspace_dir": ws},
    )
    assert r.success
    assert r.result["newTodos"][0]["active_form"] == "Working on x"


@pytest.mark.asyncio
async def test_todo_write_validation_error():
    tool = TodoWriteTool()
    r = await tool.execute("1", {"todos": [{"content": "", "status": "pending", "active_form": "x"}]}, {})
    assert not r.success
    assert r.error


def test_todo_write_to_dict_includes_long_usage():
    d = TodoWriteTool().to_dict()
    assert "description" in d
    assert TODO_WRITE_TOOL_USAGE_FOR_DESCRIPTION[:40] in d["description"]


def test_append_todos_snapshot_to_prompt(tmp_path):
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)
    p = resolve_todos_storage_path(session_key="x", session_id="sid", agent_workspace_dir=ws)
    assert p
    write_todos_file(
        p,
        [{"content": "A", "status": "pending", "active_form": "Doing A"}],
    )
    out = append_todos_snapshot_to_prompt(
        "bootstrap",
        session_key="x",
        session_id="sid",
        agent_workspace_dir=ws,
    )
    assert "bootstrap" in out
    assert "Current todo list" in out
    assert "[pending]" in out


def test_format_todos_snapshot_shared_flag():
    s = format_todos_snapshot_for_system(
        [{"content": "c", "status": "pending", "active_form": "x"}],
        shared_scope=True,
    )
    assert "orch" in s or "编排" in s


def test_read_todos_legacy_list_format(tmp_path):
    p = str(tmp_path / "t.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump([{"content": "L", "status": "pending", "active_form": "a"}], f)
    assert len(read_todos_file(p)) == 1
