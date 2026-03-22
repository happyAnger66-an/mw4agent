"""channels feishu add CLI."""

from __future__ import annotations

import json

import click
from click.testing import CliRunner

from mw4agent.cli.channels.register import register_channels_cli
from mw4agent.cli.context import create_program_context
from mw4agent.config.root import read_root_config


def _build_cli() -> click.Group:
    @click.group()
    def cli() -> None:
        return None

    register_channels_cli(cli, create_program_context("0.0.0"))
    return cli


def test_feishu_add_writes_channels_section(tmp_path, monkeypatch) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    # Avoid encrypted mw4agent.json (would make raw json.loads on file fail in CI).
    monkeypatch.delenv("MW4AGENT_SECRET_KEY", raising=False)

    runner = CliRunner()
    res = runner.invoke(
        _build_cli(),
        [
            "channels",
            "feishu",
            "add",
            "--app-id",
            "cli_app_1",
            "--app-secret",
            "secret_value_9",
            "--connection-mode",
            "websocket",
        ],
    )
    assert res.exit_code == 0, res.output

    assert (cfg_dir / "mw4agent.json").exists()
    data = read_root_config()
    feishu = (data.get("channels") or {}).get("feishu") or {}
    assert feishu.get("app_id") == "cli_app_1"
    assert feishu.get("app_secret") == "secret_value_9"
    assert feishu.get("connection_mode") == "websocket"


def test_feishu_add_json_output_redacts_secret(tmp_path, monkeypatch) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.delenv("MW4AGENT_SECRET_KEY", raising=False)

    runner = CliRunner()
    res = runner.invoke(
        _build_cli(),
        ["channels", "feishu", "add", "--app-id", "a", "--app-secret", "b", "--json"],
    )
    assert res.exit_code == 0, res.output
    raw = res.output
    brace = raw.find("{")
    assert brace >= 0, raw
    out = json.loads(raw[brace:])
    assert out.get("ok") is True
    assert out["feishu"]["app_secret"] == "********"
    assert out["feishu"]["app_id"] == "a"
