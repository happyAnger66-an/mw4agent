"""Tests for plugin discovery, tools loading, and plugin skills."""

from pathlib import Path

import pytest

from mw4agent.agents.tools import ToolRegistry, get_tool_registry
from mw4agent.plugin import (
    discover_plugins,
    load_plugins,
    get_plugin_skill_source,
    PluginInfo,
    PluginSkillSource,
)
from mw4agent.agents.skills.snapshot import build_skill_snapshot


FIXTURES_PLUGINS = Path(__file__).resolve().parent / "fixtures" / "plugins"
ECHO_PLUGIN_ROOT = FIXTURES_PLUGINS / "echo_plugin"
SKILL_PLUGIN_ROOT = FIXTURES_PLUGINS / "skill_plugin"


def test_discover_plugins_empty_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("MW4AGENT_PLUGIN_DIR", raising=False)
    infos = discover_plugins()
    assert infos == []


def test_discover_plugins_from_explicit_dirs():
    infos = discover_plugins(plugin_dirs=[FIXTURES_PLUGINS])
    assert len(infos) >= 1
    names = [p.name for p in infos]
    assert "echo-plugin" in names
    echo_info = next(p for p in infos if p.name == "echo-plugin")
    assert echo_info.root == ECHO_PLUGIN_ROOT
    assert echo_info.tools_module == "tools"


def test_discover_plugins_single_root_as_plugin(tmp_path, monkeypatch):
    (tmp_path / "plugin.json").write_text('{"name": "single"}', encoding="utf-8")
    infos = discover_plugins(plugin_dirs=[tmp_path])
    assert len(infos) == 1
    assert infos[0].name == "single"
    assert infos[0].root == tmp_path


def test_load_plugins_registers_tools():
    reg = ToolRegistry()
    # Builtin are not in this registry; only load plugin
    infos = load_plugins(plugin_dirs=[ECHO_PLUGIN_ROOT], registry=reg)
    assert len(infos) == 1
    assert infos[0].name == "echo-plugin"
    tool = reg.get_tool("echo")
    assert tool is not None
    assert tool.name == "echo"


def test_load_plugins_duplicate_tool_name_fails(tmp_path):
    (tmp_path / "plugin.json").write_text(
        '{"name": "dup", "tools_module": "tools"}',
        encoding="utf-8",
    )
    (tmp_path / "tools.py").write_text(
        """
from mw4agent.agents.tools.base import AgentTool, ToolResult

class ReadTool(AgentTool):
    def __init__(self):
        super().__init__(name="read", description="Fake", parameters={})
    async def execute(self, *a, **k):
        return ToolResult(success=True, result={})

def register_tools(registry=None):
    from mw4agent.agents.tools import get_tool_registry
    (registry or get_tool_registry()).register(ReadTool())
""",
        encoding="utf-8",
    )
    reg = ToolRegistry()
    from mw4agent.agents.tools.read_tool import ReadTool as BuiltinRead
    reg.register(BuiltinRead())  # registry already has "read"
    with pytest.raises(ValueError, match="already registered"):
        load_plugins(plugin_dirs=[tmp_path], registry=reg)


# --- Plugin skills (phase 2) ---


def test_plugin_skill_source_read_all_skills():
    source = PluginSkillSource()
    source.add_dir(SKILL_PLUGIN_ROOT / "skills")
    all_skills = source.read_all_skills()
    assert "hello" in all_skills
    assert "Say hello (plugin skill)" in (all_skills["hello"].get("description") or "")


def test_load_plugins_adds_skills_dir():
    source = get_plugin_skill_source()
    source._dirs.clear()
    load_plugins(plugin_dirs=[SKILL_PLUGIN_ROOT], registry=ToolRegistry())
    all_skills = source.read_all_skills()
    assert "hello" in all_skills


def test_build_skill_snapshot_merges_plugin_skills(tmp_path, monkeypatch):
    from mw4agent import skills as skills_module
    empty_mgr = type("Mgr", (), {"read_all_skills": lambda: {}})()
    monkeypatch.setattr(skills_module, "get_default_skill_manager", lambda: empty_mgr)
    source = get_plugin_skill_source()
    source._dirs.clear()
    source.add_dir(SKILL_PLUGIN_ROOT / "skills")
    snapshot = build_skill_snapshot()
    names = [s["name"] for s in snapshot["skills"]]
    assert "hello" in names
    assert "Say hello" in snapshot["prompt"] or ""


# --- Phase 3: config ---


def test_discover_plugins_from_config(monkeypatch):
    """When MW4AGENT_PLUGIN_DIR is unset, plugin_dirs are read from root config."""
    monkeypatch.delenv("MW4AGENT_PLUGIN_DIR", raising=False)
    from mw4agent.config import root as config_root
    monkeypatch.setattr(
        config_root,
        "read_root_section",
        lambda section, default=None: {"plugin_dirs": [str(FIXTURES_PLUGINS)]} if section == "plugins" else (default or {}),
    )
    infos = discover_plugins()
    assert len(infos) >= 1
    assert any(p.name == "echo-plugin" for p in infos)


def test_load_plugins_respects_plugins_enabled(monkeypatch):
    """When plugins_enabled is set in config, only those plugins are loaded."""
    from mw4agent.plugin.loader import _get_plugins_enabled_from_config
    monkeypatch.setattr(
        "mw4agent.plugin.loader._get_plugins_enabled_from_config",
        lambda: ["skill-plugin"],
    )
    reg = ToolRegistry()
    source = get_plugin_skill_source()
    source._dirs.clear()
    infos = load_plugins(plugin_dirs=[FIXTURES_PLUGINS], registry=reg)
    assert len(infos) == 1
    assert infos[0].name == "skill-plugin"
    assert reg.get_tool("echo") is None
    assert "hello" in source.read_all_skills()
