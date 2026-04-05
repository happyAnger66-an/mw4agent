"""exec / execute_sh tools: alias name and script parameter."""

from __future__ import annotations

import asyncio
import os

import pytest

from orbit.agents.tools.exec_tool import ExecTool


@pytest.mark.asyncio
async def test_exec_accepts_script_instead_of_command(tmp_path) -> None:
    tool = ExecTool()
    ws = str(tmp_path)
    r = await tool.execute("t1", {"script": "echo hi"}, context={"workspace_dir": ws})
    assert r.success
    assert (r.result or {}).get("stdout", "").strip() == "hi"


@pytest.mark.asyncio
async def test_execute_sh_tool_name_and_script_only(tmp_path) -> None:
    tool = ExecTool(tool_name="execute_sh")
    assert tool.name == "execute_sh"
    ws = str(tmp_path)
    r = await tool.execute("t2", {"script": "pwd"}, context={"workspace_dir": ws})
    assert r.success
    out = (r.result or {}).get("stdout", "").strip()
    assert os.path.normpath(out) == os.path.normpath(ws)
