"""Console channel plugin.

Inbound: stdin lines
Outbound: stdout prints
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass

from ..dock import ChannelDock
from ..mention_gating import resolve_mention_gating
from ..types import ChannelCapabilities, ChannelMeta, InboundContext, OutboundPayload
from .base import ChannelPlugin, InboundHandler


@dataclass(frozen=True)
class ConsoleChannel(ChannelPlugin):
    def __init__(self):
        caps = ChannelCapabilities(chat_types=("direct",), native_commands=True, block_streaming=False)
        dock = ChannelDock(id="console", capabilities=caps, resolve_require_mention=lambda _acct: False)
        meta = ChannelMeta(id="console", label="Console", docs_path="/channels/console")
        super().__init__(id="console", meta=meta, capabilities=caps, dock=dock)

    async def run_monitor(self, *, on_inbound: InboundHandler) -> None:
        # Simple stdin line reader. Exit on EOF or /quit.
        loop = asyncio.get_running_loop()

        async def read_line() -> str | None:
            return await loop.run_in_executor(None, sys.stdin.readline)

        sys.stdout.write("[mw4agent] console channel started. Type '/quit' to exit.\n")
        sys.stdout.flush()

        while True:
            raw = await read_line()
            if raw is None:
                return
            line = raw.rstrip("\n")
            if not line:
                continue
            if line.strip().lower() in ("/quit", "/exit"):
                return

            # For console we treat everything as authorized and "mentioned".
            ctx = InboundContext(
                channel="console",
                text=line,
                session_key="console:main",
                session_id="console-main",
                agent_id="main",
                chat_type="direct",
                was_mentioned=True,
                command_authorized=True,
                sender_is_owner=True,
                sender_id="local",
                sender_name="local",
                timestamp_ms=int(time.time() * 1000),
            )

            # Mention gating hook exists for parity (no-op for console).
            gate = resolve_mention_gating(
                require_mention=self.dock.require_mention(None),
                can_detect_mention=True,
                was_mentioned=ctx.was_mentioned,
            )
            if gate.should_skip:
                continue

            await on_inbound(ctx)

    async def deliver(self, payload: OutboundPayload) -> None:
        prefix = "ERR" if payload.is_error else "AI"
        sys.stdout.write(f"[{prefix}] {payload.text}\n")
        sys.stdout.flush()

