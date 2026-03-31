import types

import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mw4agent.agents.tools.base import AgentTool
from mw4agent.agents.tools.policy import (
    ToolPolicyConfig,
    SandboxToolPolicy,
    filter_tools_by_policy,
    filter_tools_by_sandbox_policy,
    resolve_effective_policy_for_context,
    resolve_effective_allow_patterns,
    resolve_sandbox_tool_policy_config,
)


class DummyCfgManager:
    def __init__(self, tools_cfg: dict) -> None:
        self._tools_cfg = tools_cfg

    def read_config(self, section: str, default=None):
        if section == "tools":
            return self._tools_cfg
        return default


class DummyTool(AgentTool):
    async def execute(self, tool_call_id, params, context=None):
        raise NotImplementedError()


def _make_tools():
    return [
        DummyTool(name="read", description="", parameters={}, owner_only=False),
        DummyTool(name="write", description="", parameters={}, owner_only=False),
        DummyTool(name="memory_search", description="", parameters={}, owner_only=False),
        DummyTool(name="gateway_ls", description="", parameters={}, owner_only=True),
    ]


def test_filter_tools_by_channel_and_user_owner():
    tools_cfg = {
        "profile": "coding",  # base
        "by_channel": {
            "feishu": {"profile": "coding", "deny": ["write"]},
        },
        "by_user": {
            "owner:local": {"profile": "full"},
        },
        "by_channel_user": {
            "feishu:ou_owner": {"profile": "full", "deny": ["gateway_ls"]},
        },
    }
    cfg_mgr = DummyCfgManager(tools_cfg)

    # Global base policy
    base = ToolPolicyConfig(profile="coding")
    tools = _make_tools()

    # 1) 普通 feishu 用户：应用 by_channel.feishu，deny write
    eff1 = resolve_effective_policy_for_context(
        cfg_mgr,
        base_policy=base,
        channel="feishu",
        user_id="ou_normal",
        sender_is_owner=False,
        command_authorized=True,
    )
    allowed1 = filter_tools_by_policy(tools, eff1)
    names1 = sorted(t.name for t in allowed1)
    assert "read" in names1
    assert "memory_search" in names1
    assert "write" not in names1

    # 2) 全局 owner:local（不区分 channel），profile=full
    eff2 = resolve_effective_policy_for_context(
        cfg_mgr,
        base_policy=base,
        channel="console",
        user_id="local",
        sender_is_owner=True,
        command_authorized=True,
    )
    allowed2 = filter_tools_by_policy(tools, eff2)
    names2 = sorted(t.name for t in allowed2)
    # full profile → 所有工具通过 policy 过滤（后续由 owner_only 控制暴露）
    assert set(names2) == {"read", "write", "memory_search", "gateway_ls"}

    # 3) feishu:ou_owner 走 by_channel_user 优先级
    eff3 = resolve_effective_policy_for_context(
        cfg_mgr,
        base_policy=base,
        channel="feishu",
        user_id="ou_owner",
        sender_is_owner=True,
        command_authorized=True,
    )
    allowed3 = filter_tools_by_policy(tools, eff3)
    names3 = sorted(t.name for t in allowed3)
    # deny gateway_ls 只影响这一组合
    assert "gateway_ls" not in names3
    assert "read" in names3
    assert "write" in names3


def test_resolve_effective_allow_patterns_full_profile():
    p = ToolPolicyConfig(profile="full", allow=None, deny=None)
    assert resolve_effective_allow_patterns(p) == ["*"]


def test_coding_profile_allows_feishu_plugin_tools_via_glob():
    tools = _make_tools() + [
        DummyTool(
            name="feishu_fetch_doc",
            description="",
            parameters={},
            owner_only=False,
        ),
    ]
    pol = ToolPolicyConfig(profile="coding")
    allowed = filter_tools_by_policy(tools, pol)
    names = {t.name for t in allowed}
    assert "feishu_fetch_doc" in names
    assert "read" in names


def test_sandbox_tool_policy_directory_isolation_defaults():
    p = SandboxToolPolicy(enabled=True, allow=["read"], deny=None, directory_isolation=None)
    assert p.should_isolate_directories(run_sandbox_request=False) is True
    assert p.should_isolate_directories(run_sandbox_request=True) is True

    p2 = SandboxToolPolicy(enabled=True, directory_isolation=False)
    assert p2.should_isolate_directories(run_sandbox_request=True) is False

    p3 = SandboxToolPolicy(enabled=False, directory_isolation=None)
    assert p3.should_isolate_directories(run_sandbox_request=True) is True


def test_filter_tools_by_sandbox_policy_deny_wins():
    tools = _make_tools()
    sb = SandboxToolPolicy(enabled=True, allow=["*"], deny=["write"])
    out = filter_tools_by_sandbox_policy(tools, sb)
    assert {t.name for t in out} == {"read", "memory_search", "gateway_ls"}


def test_resolve_sandbox_tool_policy_config_execution_isolation():
    cfg_mgr = DummyCfgManager(
        {
            "sandbox": {
                "enabled": True,
                "executionIsolation": "wasm",
                "directoryIsolation": False,
            }
        }
    )
    sb = resolve_sandbox_tool_policy_config(cfg_mgr)
    assert sb.enabled is True
    assert sb.execution_isolation == "wasm"
    assert sb.directory_isolation is False

