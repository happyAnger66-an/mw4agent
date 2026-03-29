from __future__ import annotations

from pathlib import Path

from mw4agent.config.root import write_root_config
from mw4agent.gateway.wait_timeout import (
    DEFAULT_AGENT_WAIT_TIMEOUT_MS,
    resolve_agent_wait_timeout_ms,
    rpc_client_timeout_ms,
)


def test_resolve_agent_wait_timeout_ms_rpc_explicit() -> None:
    assert resolve_agent_wait_timeout_ms(5000) == 5000
    assert resolve_agent_wait_timeout_ms(0) == 0


def test_resolve_default_two_hours(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("MW4AGENT_GATEWAY_AGENT_WAIT_TIMEOUT_MS", raising=False)
    assert resolve_agent_wait_timeout_ms(None) == DEFAULT_AGENT_WAIT_TIMEOUT_MS
    assert DEFAULT_AGENT_WAIT_TIMEOUT_MS == 2 * 60 * 60 * 1000


def test_resolve_from_config_and_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MW4AGENT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("MW4AGENT_GATEWAY_AGENT_WAIT_TIMEOUT_MS", raising=False)
    write_root_config({"gateway": {"agentWaitTimeoutMs": 99_000}})
    assert resolve_agent_wait_timeout_ms(None) == 99_000

    monkeypatch.setenv("MW4AGENT_GATEWAY_AGENT_WAIT_TIMEOUT_MS", "12345")
    assert resolve_agent_wait_timeout_ms(None) == 12345


def test_rpc_client_timeout_ms_padding() -> None:
    assert rpc_client_timeout_ms(1000) >= 60_000
    assert rpc_client_timeout_ms(7200_000) == 7200_000 + 120_000
