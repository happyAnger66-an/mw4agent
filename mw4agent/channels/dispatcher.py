"""Channel dispatcher: inbound -> agent -> outbound.

This is the MW4Agent analogue of OpenClaw's dispatchInboundMessage + getReplyFromConfig path,
but heavily simplified for the first console channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..agents.runner.runner import AgentRunner
from ..agents.session.manager import SessionManager
from ..agents.types import AgentRunParams
from .mention_gating import resolve_mention_gating
from .registry import ChannelRegistry, get_channel_registry
from .types import InboundContext, OutboundPayload


@dataclass(frozen=True)
class ChannelRuntime:
    session_manager: SessionManager
    agent_runner: AgentRunner


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

