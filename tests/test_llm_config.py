"""Tests for configurable LLM provider/model via encrypted config."""

from __future__ import annotations

from pathlib import Path

from mw4agent.agents.types import AgentRunParams
from mw4agent.config import ConfigManager, get_default_config_manager
from mw4agent.llm.backends import generate_reply


def test_llm_config_provider_and_model_precedence(monkeypatch, tmp_path: Path) -> None:
    """Config-driven provider/model should be used when params/env are not set."""
    # Isolate config directory
    cfg_dir = tmp_path / "config"
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("MW4AGENT_STATE_DIR", str(tmp_path / "mw_state"))

    # Reset default config manager singleton
    import mw4agent.config.manager as cfg_mod

    cfg_mod._default_config_manager = None  # type: ignore[attr-defined]

    # Ensure env does not override
    monkeypatch.delenv("MW4AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MW4AGENT_LLM_MODEL", raising=False)

    mgr: ConfigManager = get_default_config_manager()
    mgr.write_config(
        "llm",
        {
            "provider": "echo",
            "model": "test-model-from-config",
        },
    )

    text, provider, model, usage = generate_reply(AgentRunParams(message="hi"))
    assert provider == "echo"
    assert model == "test-model-from-config"

