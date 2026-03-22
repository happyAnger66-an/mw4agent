"""Interactive configuration wizard: per-agent LLM section."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mw4agent.cli.configuration import CONFIG_SECTION_CHOICES, _run_agent_llm_config


def test_wizard_section_choices_include_agent_llm() -> None:
    keys = [key for _label, key in CONFIG_SECTION_CHOICES]
    assert "agent_llm" in keys
    labels = [label for label, _key in CONFIG_SECTION_CHOICES]
    assert any("per-agent" in lbl.lower() or "agent llm" in lbl.lower() for lbl in labels)


def test_run_agent_llm_config_writes_agent_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MW4AGENT_IS_ENC", "0")
    monkeypatch.delenv("MW4AGENT_SECRET_KEY", raising=False)

    monkeypatch.setattr(
        "mw4agent.cli.configuration._prompt_provider_list",
        lambda _current: "echo",
    )

    class _Sel:
        @staticmethod
        def ask():
            return "coders"

    try:
        import questionary

        monkeypatch.setattr(questionary, "select", lambda *a, **kw: _Sel())
    except ImportError:
        pass

    def cp(message, *args, **kwargs):
        msg = str(message)
        if "Agent id" in msg:
            return "coders"
        if msg.strip() == "Model ID":
            return "wizard-unit-model"
        if "Base URL" in msg:
            return ""
        if "API Key" in msg:
            return ""
        return kwargs.get("default", "")

    monkeypatch.setattr("mw4agent.cli.configuration.click.prompt", cp)

    _run_agent_llm_config()

    agent_json = Path(tmp_path) / ".mw4agent" / "agents" / "coders" / "agent.json"
    assert agent_json.is_file()
    data = json.loads(agent_json.read_text(encoding="utf-8"))
    assert data.get("llm", {}).get("provider") == "echo"
    assert data.get("llm", {}).get("model_id") == "wizard-unit-model"
