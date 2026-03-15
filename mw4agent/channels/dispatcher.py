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
from ..log import get_logger
from .mention_gating import resolve_mention_gating
from .registry import ChannelRegistry, get_channel_registry
from .types import InboundContext, OutboundPayload

logger = get_logger(__name__)


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
            logger.error("unknown channel: %s", ctx.channel)
            raise ValueError(f"Unknown channel: {ctx.channel}")

        dock = self.registry.get_dock(ctx.channel)
        require_mention = dock.require_mention(None) if dock else True
        # In console and most text channels we can always detect mention.
        gate = resolve_mention_gating(
            require_mention=require_mention if ctx.chat_type == "group" else False,
            can_detect_mention=True,
            was_mentioned=ctx.was_mentioned,
        )
        logger.debug("dispatch_inbound channel=%s gate.should_skip=%s", ctx.channel, gate.should_skip)
        if gate.should_skip:
            logger.debug("skipping message due to mention gating")
            return

        # Call agent via Gateway RPC (aligned with OpenClaw) or direct AgentRunner
        if self.runtime.gateway_base_url:
            logger.debug("calling agent via gateway: %s", self.runtime.gateway_base_url)
            result_text = await self._call_agent_via_gateway(ctx)
        else:
            logger.debug("calling agent direct")
            result_text = await self._call_agent_direct(ctx)

        if result_text:
            logger.info("channel=%s reply length=%s", ctx.channel, len(result_text))
            # 将入站 extra（chat_id/message_id）与 session_id 传给 deliver，供 Feishu 等回发到正确会话
            extra = {
                "inbound": {
                    "extra": ctx.extra if isinstance(ctx.extra, dict) else {},
                    "session_id": ctx.session_id,
                }
            }
            await plugin.deliver(
                OutboundPayload(
                    text=result_text,
                    is_error=False,
                    extra=extra,
                )
            )
        else:
            logger.warning("channel=%s agent returned empty reply", ctx.channel)

    async def _call_agent_via_gateway(self, ctx: InboundContext) -> Optional[str]:
        """Call agent via Gateway RPC (aligned with OpenClaw)."""
        base_url = self.runtime.gateway_base_url or "http://127.0.0.1:18790"
        idem_key = str(uuid.uuid4())

        # Call agent RPC
        agent_params = {
            "message": ctx.text,
            "sessionKey": ctx.session_key,
            "sessionId": ctx.session_id,
            "agentId": ctx.agent_id,
            "idempotencyKey": idem_key,
        }
        try:
            start_res = call_rpc(base_url=base_url, method="agent", params=agent_params, timeout_ms=30000)
        except Exception as e:
            logger.error("gateway agent RPC failed: %s", e, exc_info=True)
            raise

        run_id = start_res.get("runId")
        if not run_id:
            logger.warning("gateway agent returned no runId: %s", start_res)
            return None

        # Wait for completion
        try:
            wait_res = call_rpc(
                base_url=base_url,
                method="agent.wait",
                params={"runId": run_id, "timeoutMs": 30000},
                timeout_ms=32000,
            )
        except Exception as e:
            logger.error("gateway agent.wait RPC failed: %s", e, exc_info=True)
            raise

        payload = wait_res.get("payload", {})
        if payload.get("status") != "ok":
            error = payload.get("error")
            logger.warning("gateway agent.wait status not ok: %s", error or payload)
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
        try:
            result = await self.runtime.agent_runner.run(params)
        except Exception as e:
            logger.error("agent direct run failed: %s", e, exc_info=True)
            raise
        if not result.payloads:
            logger.debug("agent direct returned no payloads")
            return None
        # Collect all text payloads
        texts = []
        for payload in result.payloads:
            if payload.text:
                texts.append(payload.text)
        return "\n".join(texts) if texts else None

    async def run_channel(self, channel_id: str) -> None:
        plugin = self.registry.get_plugin(channel_id)
        if not plugin:
            logger.error("run_channel unknown channel: %s", channel_id)
            raise ValueError(f"Unknown channel: {channel_id}")
        logger.info("run_channel starting: %s", channel_id)
        await plugin.run_monitor(on_inbound=self.dispatch_inbound)
        logger.debug("run_channel ended: %s", channel_id)

