"""E2E test: console inbound -> Gateway -> AgentRunner -> mock LLM -> reply.

This test wires together:
- Mock LLM server (OpenAI-compatible REST API)
- Gateway HTTP RPC (agent / agent.wait)
- ChannelDispatcher in Gateway mode for the `console` channel
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
import uvicorn

from mw4agent.agents.runner.runner import AgentRunner
from mw4agent.agents.session.manager import SessionManager
from mw4agent.channels.dispatcher import ChannelDispatcher, ChannelRuntime
from mw4agent.channels.registry import ChannelRegistry
from mw4agent.channels.dock import ChannelDock
from mw4agent.channels.types import (
    ChannelCapabilities,
    ChannelMeta,
    InboundContext,
    OutboundPayload,
)
from mw4agent.channels.plugins.base import ChannelPlugin
from mw4agent.config import get_default_config_manager
from mw4agent.llm.mock_server import create_app as create_mock_llm_app


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_health(base_url: str, deadline_s: float = 8.0) -> dict:
    start = time.time()
    while time.time() - start < deadline_s:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("gateway did not become healthy in time")


@pytest.fixture
def temp_session_file(tmp_path: Path) -> Path:
    return tmp_path / "sessions.json"


@pytest.fixture
def session_manager(temp_session_file: Path) -> SessionManager:
    return SessionManager(str(temp_session_file))


@pytest.fixture
def agent_runner(session_manager: SessionManager) -> AgentRunner:
    return AgentRunner(session_manager)


@pytest.fixture
def mock_llm_server():
    """Run the mock LLM server (OpenAI-compatible) on a free port."""
    port = _find_free_port()
    app = create_mock_llm_app()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    # Run server in a background process to avoid blocking the test loop.
    import multiprocessing

    def _run():
        server.run()

    proc = multiprocessing.Process(target=_run, daemon=True)
    proc.start()

    base_url = f"http://127.0.0.1:{port}"

    # Wait for the mock server to become responsive on /v1/chat/completions
    start = time.time()
    last_err: Exception | None = None
    while time.time() - start < 8.0:
        try:
            body = {
                "model": "mock-gpt",
                "messages": [{"role": "user", "content": "ping"}],
            }
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url=f"{base_url}/v1/chat/completions",
                data=data,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    break
        except Exception as e:  # pragma: no cover - startup race
            last_err = e
            time.sleep(0.15)
    else:
        proc.terminate()
        proc.join(timeout=3)
        raise RuntimeError(f"mock llm server did not become ready: {last_err}")

    try:
        yield port
    finally:
        proc.terminate()
        proc.join(timeout=3)


@pytest.fixture
def gateway_with_mock_llm(tmp_path: Path, mock_llm_server: int):
    """Start Gateway configured to use the mock LLM server."""
    mock_port = mock_llm_server

    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    session_file = str(tmp_path / "gateway.sessions.json")

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)

    # Prepare environment for the gateway process.
    env = dict(os.environ)
    env["MW4AGENT_CONFIG_DIR"] = str(cfg_dir)
    env["MW4AGENT_OPENAI_BASE_URL"] = f"http://127.0.0.1:{mock_port}"
    env["OPENAI_API_KEY"] = "test-key"

    # Write root config with llm section (single file ~/.mw4agent/mw4agent.json or MW4AGENT_CONFIG_DIR/mw4agent.json).
    # Gateway subprocess has MW4AGENT_CONFIG_DIR=cfg_dir, so it will read cfg_dir/mw4agent.json.
    root_config = cfg_dir / "mw4agent.json"
    root_config.parent.mkdir(parents=True, exist_ok=True)
    root_config.write_text(
        json.dumps({
            "llm": {
                "provider": "openai",
                "model": "mock-gpt",
                "base_url": f"http://127.0.0.1:{mock_port}",
            },
        }),
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        "-m",
        "mw4agent",
        "gateway",
        "run",
        "--bind",
        "127.0.0.1",
        "--port",
        str(port),
        "--session-file",
        session_file,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    try:
        _wait_for_health(base_url)
        yield (proc, base_url, port)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def channel_registry() -> ChannelRegistry:
    return ChannelRegistry()


class _CapturePlugin(ChannelPlugin):
    """Console-like plugin that captures delivered payload text."""

    def __init__(self, delivered: list[str]) -> None:
        caps = ChannelCapabilities(
            chat_types=("direct", "group"),
            native_commands=True,
            block_streaming=False,
        )
        dock = ChannelDock(
            id="console",
            capabilities=caps,
            resolve_require_mention=lambda _acct: False,
        )
        meta = ChannelMeta(id="console", label="Console(Test)", docs_path="/channels/console")
        super().__init__(id="console", meta=meta, capabilities=caps, dock=dock)
        self._delivered = delivered

    async def run_monitor(self, *, on_inbound):  # pragma: no cover
        raise RuntimeError("not implemented for tests")

    async def deliver(self, payload: OutboundPayload) -> None:
        self._delivered.append(payload.text)


@pytest.mark.asyncio
async def test_console_gateway_with_mock_llm(
    tmp_path: Path,
    gateway_with_mock_llm,
    session_manager: SessionManager,
    agent_runner: AgentRunner,
    channel_registry: ChannelRegistry,
) -> None:
    """Console inbound -> Gateway -> Agent -> mock LLM -> reply."""
    proc, gateway_base_url, gateway_port = gateway_with_mock_llm

    # Wire ChannelDispatcher in Gateway mode for the console channel.
    runtime = ChannelRuntime(
        session_manager=session_manager,
        agent_runner=agent_runner,
        gateway_base_url=gateway_base_url,
    )
    dispatcher = ChannelDispatcher(runtime=runtime, registry=channel_registry)

    # Register capture plugin once.
    delivered_messages: list[str] = []
    channel_registry.register_plugin(_CapturePlugin(delivered_messages))

    # Simulate console inbound message.
    user_text = "Hello from console to mock LLM"
    ctx = InboundContext(
        channel="console",
        text=user_text,
        session_key="e2e:console-mock",
        session_id="e2e-console-mock",
        agent_id="e2e",
        chat_type="private",
        was_mentioned=True,
    )

    await dispatcher.dispatch_inbound(ctx)

    # Assert that we got a reply and that LLM pipeline ran end-to-end.
    assert delivered_messages, "No messages were delivered via console channel"
    combined = " ".join(delivered_messages)
    # 至少要包含用户原始文本，且不是空回复
    assert "Hello from console to mock LLM" in combined
    # 不应包含明显的错误标记
    assert "openai-error" not in combined

