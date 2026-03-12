"""Channel dispatcher: inbound -> agent -> outbound.

This is the MW4Agent analogue of OpenClaw's dispatchInboundMessage + getReplyFromConfig path.

Design:
- If `gateway_base_url` is provided, channels call agent via Gateway RPC (aligned with OpenClaw).
- Otherwise, channels call AgentRunner directly (simplified mode for testing/development).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from ..agents.runner.runner import AgentRunner
from ..agents.session.manager import SessionManager
from ..agents.types import AgentRunParams
from ..gateway.client import call_rpc
from .mention_gating import resolve_mention_gating
from .registry import ChannelRegistry, get_channel_registry
from .types import InboundContext, OutboundPayload


@dataclass(frozen=True)
class ChannelRuntime:
    session_manager: SessionManager
    agent_runner: AgentRunner
    gateway_base_url: Optional[str] = None  # If set, use Gateway RPC; otherwise direct AgentRunner


class ChannelDispatcher:
    def __init__(self, runtime: ChannelRuntime, registry: Optional[ChannelRegistry] = None) -> None:
        self.runtime = runtime
        self.registry = registry or get_channel_registry()

    async def dispatch_inbound(self, ctx: InboundContext) -> None:
        plugin = self.registry.get_plugin(ctx.channel)
        if not plugin:
            raise ValueError(f"Unknown channel: {ctx.channel}")

        dock = self.registry.get_dock(ctx.channel)
        require_mention = dock.require_mention(None) if dock else True
        # In console and most text channels we can always detect mention.
        gate = resolve_mention_gating(
            require_mention=require_mention if ctx.chat_type == "group" else False,
            can_detect_mention=True,
            was_mentioned=ctx.was_mentioned,
        )
        if gate.should_skip:
            return

        # Call agent via Gateway RPC (aligned with OpenClaw) or direct AgentRunner
        if self.runtime.gateway_base_url:
            # Gateway RPC path (aligned with OpenClaw design)
            result_text = await self._call_agent_via_gateway(ctx)
        else:
            # Direct AgentRunner path (simplified mode)
            result_text = await self._call_agent_direct(ctx)

        if result_text:
            await plugin.deliver(
                OutboundPayload(
                    text=result_text,
                    is_error=False,
                    extra={},
                )
            )

    async def _call_agent_via_gateway(self, ctx: InboundContext) -> Optional[str]:
        """Call agent via Gateway RPC (aligned with OpenClaw)."""
        base_url = self.runtime.gateway_base_url or "http://127.0.0.1:18789"
        idem_key = str(uuid.uuid4())

        # Call agent RPC
        agent_params = {
            "message": ctx.text,
            "sessionKey": ctx.session_key,
            "sessionId": ctx.session_id,
            "agentId": ctx.agent_id,
            "idempotencyKey": idem_key,
        }
        start_res = call_rpc(base_url=base_url, method="agent", params=agent_params, timeout_ms=30000)

        run_id = start_res.get("runId")
        if not run_id:
            return None

        # Wait for completion
        wait_res = call_rpc(
            base_url=base_url,
            method="agent.wait",
            params={"runId": run_id, "timeoutMs": 30000},
            timeout_ms=32000,
        )

        payload = wait_res.get("payload", {})
        if payload.get("status") != "ok":
            error = payload.get("error")
            return f"[Error: {error}]" if error else None

        # Extract reply text from payload
        reply_text = payload.get("replyText") or ""
        return reply_text.strip() if reply_text else None

    async def _call_agent_direct(self, ctx: InboundContext) -> Optional[str]:
        """Call AgentRunner directly (simplified mode)."""
        params = AgentRunParams(
            message=ctx.text,
            session_key=ctx.session_key,
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            deliver=False,
            channel=ctx.channel,
        )
        result = await self.runtime.agent_runner.run(params)
        if not result.payloads:
            return


        # Resolve plugin for delivery (direct-mode delivery is handled here).
        plugin = self.registry.get_plugin(ctx.channel)
        if not plugin:
            raise ValueError(f"Unknown channel: {ctx.channel}")

        # 将入站上下文的一部分透传到 OutboundPayload.extra，方便插件决定“发到哪里”。
        inbound_for_plugin = {
            "channel": ctx.channel,
            "session_key": ctx.session_key,
            "session_id": ctx.session_id,
            "agent_id": ctx.agent_id,
            "chat_type": ctx.chat_type,
            "sender_id": ctx.sender_id,
            "sender_name": ctx.sender_name,
            "to": ctx.to,
            "thread_id": ctx.thread_id,
            "timestamp_ms": ctx.timestamp_ms,
            "extra": dict(ctx.extra),
        }

        for payload in result.payloads:
            if not payload.text:
                continue
            await plugin.deliver(
                OutboundPayload(
                    text=payload.text,
                    is_error=bool(payload.is_error),
                    extra={
                        "meta": {"status": str(result.meta.status)},
                        "inbound": inbound_for_plugin,
                    },
                )
            )

    async def run_channel(self, channel_id: str) -> None:
        plugin = self.registry.get_plugin(channel_id)
        if not plugin:
            raise ValueError(f"Unknown channel: {channel_id}")
        await plugin.run_monitor(on_inbound=self.dispatch_inbound)

