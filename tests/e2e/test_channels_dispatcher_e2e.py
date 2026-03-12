"""End-to-end pytest tests for ChannelDispatcher (Gateway RPC and direct modes)."""

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

from mw4agent.agents.runner.runner import AgentRunner
from mw4agent.agents.session.manager import SessionManager
from mw4agent.channels.dispatcher import ChannelDispatcher, ChannelRuntime
from mw4agent.channels.registry import ChannelRegistry
from mw4agent.channels.dock import ChannelDock
from mw4agent.channels.types import ChannelCapabilities, ChannelMeta, OutboundPayload
from mw4agent.channels.plugins.base import ChannelPlugin
from mw4agent.channels.types import InboundContext
from mw4agent.channels.plugins.console import ConsoleChannel


def _find_free_port() -> int:
    """Find a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_health(base_url: str, deadline_s: float = 8.0) -> dict:
    """Wait for gateway to become healthy."""
    start = time.time()
    while time.time() - start < deadline_s:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("gateway did not become healthy in time")


def _rpc_call(base_url: str, method: str, params: dict, timeout_s: float = 5.0) -> dict:
    """Call Gateway RPC."""
    body = {"id": str(uuid.uuid4()), "method": method, "params": params}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/rpc",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


@pytest.fixture
def temp_session_file(tmp_path: Path) -> Path:
    """Create a temporary session file."""
    return tmp_path / "sessions.json"


@pytest.fixture
def session_manager(temp_session_file: Path) -> SessionManager:
    """Create a SessionManager with temporary file."""
    return SessionManager(str(temp_session_file))


@pytest.fixture
def agent_runner(session_manager: SessionManager) -> AgentRunner:
    """Create an AgentRunner."""
    return AgentRunner(session_manager)


@pytest.fixture
def gateway_process(tmp_path: Path):
    """Start a gateway process for testing."""
    port = _find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    session_file = str(tmp_path / "gateway.sessions.json")

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
        env=dict(os.environ),
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
    """Create a fresh channel registry (tests should register their own plugins)."""
    return ChannelRegistry()


class _CapturePlugin(ChannelPlugin):
    """Minimal plugin that captures delivered payloads.

    We avoid re-registering the same channel id by only registering this plugin once per test.
    """

    def __init__(self, *, require_mention: bool, delivered: list[str]) -> None:
        caps = ChannelCapabilities(chat_types=("direct", "group"), native_commands=True, block_streaming=False)
        dock = ChannelDock(
            id="console",
            capabilities=caps,
            resolve_require_mention=(lambda _acct: require_mention),
        )
        meta = ChannelMeta(id="console", label="Console(Test)", docs_path="/channels/console")
        super().__init__(id="console", meta=meta, capabilities=caps, dock=dock)
        self._delivered = delivered

    async def run_monitor(self, *, on_inbound):  # pragma: no cover - not used in these tests
        raise RuntimeError("not implemented for tests")

    async def deliver(self, payload: OutboundPayload) -> None:
        self._delivered.append(payload.text)


@pytest.mark.asyncio
async def test_dispatcher_direct_mode(
    session_manager: SessionManager,
    agent_runner: AgentRunner,
    channel_registry: ChannelRegistry,
) -> None:
    """Test ChannelDispatcher in direct mode (no Gateway)."""
    runtime = ChannelRuntime(
        session_manager=session_manager,
        agent_runner=agent_runner,
        gateway_base_url=None,  # Direct mode
    )
    dispatcher = ChannelDispatcher(runtime=runtime, registry=channel_registry)

    # Create inbound context
    ctx = InboundContext(
        channel="console",
        text="Hello, agent!",
        session_key="test:direct",
        session_id="test-direct",
        agent_id="test",
        chat_type="private",
        was_mentioned=True,
    )

    # Capture delivered messages (register capture plugin once)
    delivered_messages: list[str] = []
    channel_registry.register_plugin(_CapturePlugin(require_mention=False, delivered=delivered_messages))

    # Dispatch
    await dispatcher.dispatch_inbound(ctx)

    # Verify message was delivered
    assert len(delivered_messages) > 0
    assert any("Hello" in msg or "agent" in msg.lower() for msg in delivered_messages)


@pytest.mark.asyncio
async def test_dispatcher_gateway_mode(
    session_manager: SessionManager,
    agent_runner: AgentRunner,
    channel_registry: ChannelRegistry,
    gateway_process,
) -> None:
    """Test ChannelDispatcher in Gateway RPC mode."""
    proc, base_url, port = gateway_process

    runtime = ChannelRuntime(
        session_manager=session_manager,
        agent_runner=agent_runner,
        gateway_base_url=base_url,  # Gateway mode
    )
    dispatcher = ChannelDispatcher(runtime=runtime, registry=channel_registry)

    # Create inbound context
    ctx = InboundContext(
        channel="console",
        text="Hello via Gateway!",
        session_key="test:gateway",
        session_id="test-gateway",
        agent_id="test",
        chat_type="private",
        was_mentioned=True,
    )

    delivered_messages: list[str] = []
    channel_registry.register_plugin(_CapturePlugin(require_mention=False, delivered=delivered_messages))

    # Dispatch (should go through Gateway)
    await dispatcher.dispatch_inbound(ctx)

    # Verify message was delivered
    assert len(delivered_messages) > 0
    assert any("Hello" in msg or "Gateway" in msg for msg in delivered_messages)


@pytest.mark.asyncio
async def test_dispatcher_mention_gating(
    session_manager: SessionManager,
    agent_runner: AgentRunner,
    channel_registry: ChannelRegistry,
) -> None:
    """Test mention gating in dispatcher."""
    runtime = ChannelRuntime(
        session_manager=session_manager,
        agent_runner=agent_runner,
        gateway_base_url=None,
    )
    dispatcher = ChannelDispatcher(runtime=runtime, registry=channel_registry)

    # Create context without mention in group chat
    ctx = InboundContext(
        channel="console",
        text="Hello in group",
        session_key="test:gating",
        session_id="test-gating",
        agent_id="test",
        chat_type="group",
        was_mentioned=False,  # Not mentioned
    )

    delivered_messages: list[str] = []
    # In groups, require mention = True → should skip when was_mentioned=False
    channel_registry.register_plugin(_CapturePlugin(require_mention=True, delivered=delivered_messages))

    await dispatcher.dispatch_inbound(ctx)
    assert delivered_messages == []


@pytest.mark.asyncio
async def test_dispatcher_private_chat_no_gating(
    session_manager: SessionManager,
    agent_runner: AgentRunner,
    channel_registry: ChannelRegistry,
) -> None:
    """Test that private chats don't require mentions."""
    runtime = ChannelRuntime(
        session_manager=session_manager,
        agent_runner=agent_runner,
        gateway_base_url=None,
    )
    dispatcher = ChannelDispatcher(runtime=runtime, registry=channel_registry)

    # Create private chat context
    ctx = InboundContext(
        channel="console",
        text="Hello in private",
        session_key="test:private",
        session_id="test-private",
        agent_id="test",
        chat_type="private",
        was_mentioned=False,  # Not mentioned, but private chat
    )

    delivered_messages: list[str] = []
    # Even if require_mention=True, dispatcher disables mention gating for non-group chats.
    channel_registry.register_plugin(_CapturePlugin(require_mention=True, delivered=delivered_messages))

    # Dispatch (should not be gated in private chat)
    await dispatcher.dispatch_inbound(ctx)

    # Should deliver in private chat even without mention
    assert len(delivered_messages) > 0
