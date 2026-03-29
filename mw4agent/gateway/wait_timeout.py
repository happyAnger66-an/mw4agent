"""Default and configured timeout for Gateway ``agent.wait`` (milliseconds)."""

from __future__ import annotations

import os
from typing import Any, Optional

from ..config.root import read_root_section

# 2 hours — long multi-tool agent runs should not fail by default on client wait.
DEFAULT_AGENT_WAIT_TIMEOUT_MS = 2 * 60 * 60 * 1000


def resolve_agent_wait_timeout_ms(rpc_timeout_ms: Optional[Any] = None) -> int:
    """Resolve ``agent.wait`` duration.

    1. If RPC provides non-null ``timeoutMs`` and it parses to ``>= 0``, use it (``0`` = poll-only / immediate timeout).
    2. Else ``MW4AGENT_GATEWAY_AGENT_WAIT_TIMEOUT_MS`` if set and ``> 0``.
    3. Else ``gateway.agentWaitTimeoutMs`` / ``gateway.agent_wait_timeout_ms`` in root config.
    4. Else :data:`DEFAULT_AGENT_WAIT_TIMEOUT_MS`.
    """
    if rpc_timeout_ms is not None:
        try:
            v = int(rpc_timeout_ms)
            if v >= 0:
                return v
        except (TypeError, ValueError):
            pass

    env = os.environ.get("MW4AGENT_GATEWAY_AGENT_WAIT_TIMEOUT_MS", "").strip()
    if env:
        try:
            v = int(env)
            if v > 0:
                return v
        except ValueError:
            pass

    gw = read_root_section("gateway", default={})
    if isinstance(gw, dict):
        for key in ("agentWaitTimeoutMs", "agent_wait_timeout_ms"):
            raw = gw.get(key)
            if raw is None:
                continue
            try:
                v = int(raw)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue

    return DEFAULT_AGENT_WAIT_TIMEOUT_MS


def rpc_client_timeout_ms(wait_ms: int, *, padding_ms: int = 120_000) -> int:
    """HTTP client timeout for ``agent.wait`` call: wait budget + padding (min 60s)."""
    return max(60_000, int(wait_ms) + int(padding_ms))
