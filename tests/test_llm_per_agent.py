"""Per-agent LLM provider/model via agents/<id>/agent.json llm section."""

from __future__ import annotations

from pathlib import Path

import pytest

from mw4agent.agents.agent_manager import AgentManager
from mw4agent.agents.types import AgentRunParams
from mw4agent.config import ConfigManager, get_default_config_manager
from mw4agent.llm.backends import generate_reply


@pytest.fixture
def isolated_config(monkeypatch, tmp_path: Path):
    cfg_dir = tmp_path / "config"
    state_dir = tmp_path / "mw_state"
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(state_dir))
    import mw4agent.config.manager as cfg_mod

    cfg_mod._default_config_manager = None  # type: ignore[attr-defined]
    yield cfg_dir, state_dir


def test_per_agent_llm_overrides_global_provider(isolated_config, monkeypatch) -> None:
    _, state_dir = isolated_config
    monkeypatch.delenv("MW4AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MW4AGENT_LLM_MODEL", raising=False)

    mgr_cfg: ConfigManager = get_default_config_manager()
    mgr_cfg.write_config(
        "llm",
        {"provider": "echo", "model": "global-main-model"},
    )

    am = AgentManager()
    am.get_or_create("coders")
    cfg = am.get("coders")
    assert cfg is not None
    cfg.llm = {"provider": "custom-per-agent-llm", "model": "any"}
    am.save(cfg)

    text, provider, model, _usage = generate_reply(AgentRunParams(message="hi", agent_id="coders"))
    assert provider == "custom-per-agent-llm"
    assert "custom-per-agent-llm" in text
    assert model == "any"

    # Same global config, main agent without llm block → global provider/model
    text_m, provider_m, model_m, _u2 = generate_reply(AgentRunParams(message="hi", agent_id="main"))
    assert provider_m == "echo"
    assert model_m == "global-main-model"


def test_per_agent_model_overrides_global_only_model(isolated_config, monkeypatch) -> None:
    _, _state = isolated_config
    monkeypatch.delenv("MW4AGENT_LLM_MODEL", raising=False)

    mgr_cfg: ConfigManager = get_default_config_manager()
    mgr_cfg.write_config("llm", {"provider": "echo", "model": "global-model"})

    am = AgentManager()
    am.get_or_create("sales")
    c = am.get("sales")
    assert c is not None
    c.llm = {"model": "sales-only-model"}
    am.save(c)

    _t, prov, model, _u = generate_reply(AgentRunParams(message="x", agent_id="sales"))
    assert prov == "echo"
    assert model == "sales-only-model"
