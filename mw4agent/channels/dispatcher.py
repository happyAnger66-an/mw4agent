"""Channel dispatcher: inbound -> agent -> outbound.

This is the MW4Agent analogue of OpenClaw's dispatchInboundMessage + getReplyFromConfig path.

Design:
- If `gateway_base_url` is provided, channels call agent via Gateway RPC (aligned with OpenClaw).
- Otherwise, channels call AgentRunner directly (simplified mode for testing/development).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Optional

from ..agents.types import StreamEvent as AgentStreamEvent
from ..agents.runner.runner import AgentRunner
from ..agents.session.manager import SessionManager
from ..agents.types import AgentRunParams
from ..gateway.client import call_rpc
from ..gateway.wait_timeout import resolve_agent_wait_timeout_ms, rpc_client_timeout_ms
from ..log import get_logger
from .mention_gating import resolve_mention_gating
from .registry import ChannelRegistry, get_channel_registry
from .feishu_agent_progress import (
    FEISHU_TOOL_PROGRESS_META_KEY,
    feishu_progress_updates_enabled,
    feishu_session_wants_tool_progress,
    format_agent_stream_event_for_feishu,
    parse_feishu_tool_progress_command,
)
from .feishu_llm_stream import (
    FEISHU_LLM_STREAM_META_KEY,
    feishu_session_effective_llm_stream,
    format_llm_stream_event_for_feishu,
    parse_feishu_thinking_command,
)
from .types import InboundContext, OutboundPayload

logger = get_logger(__name__)


def _is_feishu_channel(channel: str) -> bool:
    return channel == "feishu" or (channel or "").startswith("feishu:")


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

        # Feishu：/tool_exec_start | /tool_exec_stop 控制本会话是否推送工具循环进度（默认不推送）
        if _is_feishu_channel(ctx.channel):
            cmd, remainder = parse_feishu_tool_progress_command(ctx.text or "")
            if cmd is not None:
                entry = self.runtime.session_manager.get_or_create_session(
                    ctx.session_id, ctx.session_key, ctx.agent_id
                )
                meta = dict(entry.metadata or {})
                meta[FEISHU_TOOL_PROGRESS_META_KEY] = cmd
                self.runtime.session_manager.update_session(ctx.session_id, metadata=meta)
                ctx = replace(ctx, text=remainder)
                if not remainder.strip():
                    ack = (
                        "已开启工具进度推送（本会话）。Agent 调用工具时将推送 `[进度]` 消息。发送 `/tool_exec_stop` 可关闭。"
                        if cmd
                        else "已关闭工具进度推送。发送 `/tool_exec_start` 可重新开启。"
                    )
                    extra = {
                        "inbound": {
                            "extra": ctx.extra if isinstance(ctx.extra, dict) else {},
                            "session_id": ctx.session_id,
                        }
                    }
                    await plugin.deliver(
                        OutboundPayload(text=ack, is_error=False, extra=extra)
                    )
                    return

        # Feishu：/thinking | /close_thinking 控制本会话是否订阅并推送 LLM 流（思考/片段/工具计划）
        if _is_feishu_channel(ctx.channel):
            think_cmd, think_remainder = parse_feishu_thinking_command(ctx.text or "")
            if think_cmd is not None:
                entry = self.runtime.session_manager.get_or_create_session(
                    ctx.session_id, ctx.session_key, ctx.agent_id
                )
                meta = dict(entry.metadata or {})
                meta[FEISHU_LLM_STREAM_META_KEY] = think_cmd
                self.runtime.session_manager.update_session(ctx.session_id, metadata=meta)
                ctx = replace(ctx, text=think_remainder)
                if not think_remainder.strip():
                    ack = (
                        "已开启模型思考与片段推送（本会话）。Agent 回复过程中将推送 `[模型]` 消息。发送 `/close_thinking` 可关闭。"
                        if think_cmd
                        else "已关闭模型思考与片段推送。发送 `/thinking` 可重新开启。"
                    )
                    extra = {
                        "inbound": {
                            "extra": ctx.extra if isinstance(ctx.extra, dict) else {},
                            "session_id": ctx.session_id,
                        }
                    }
                    await plugin.deliver(
                        OutboundPayload(text=ack, is_error=False, extra=extra)
                    )
                    return

        # Feishu：在用户消息下添加「思考/正在输入」表情，回复完成或异常后移除（与 OpenClaw 一致）
        typing_state = None
        if _is_feishu_channel(ctx.channel) and isinstance(ctx.extra, dict):
            msg_id = ctx.extra.get("message_id") or ctx.extra.get("messageId")
            if msg_id:
                fn_begin = getattr(plugin, "feishu_typing_begin", None)
                if callable(fn_begin):
                    typing_state = await fn_begin(str(msg_id))
                else:
                    from .feishu_outbound import add_typing_indicator

                    typing_state = await add_typing_indicator(str(msg_id))

        # Direct AgentRunner: optional stable run_id for tool / llm EventStream subscriptions.
        direct_run_id: Optional[str] = None
        progress_handler = None
        llm_handler = None
        es = self.runtime.agent_runner.event_stream
        cap = getattr(plugin, "capabilities", None)
        llm_cap = cap is not None and getattr(cap, "subscribe_llm_stream", False)
        want_llm_stream = False
        if llm_cap and not self.runtime.gateway_base_url:
            if _is_feishu_channel(ctx.channel):
                want_llm_stream = feishu_session_effective_llm_stream(
                    self.runtime.session_manager, ctx.session_id
                )
            else:
                want_llm_stream = True
        feishu_tool_progress = (
            _is_feishu_channel(ctx.channel)
            and not self.runtime.gateway_base_url
            and feishu_progress_updates_enabled()
            and feishu_session_wants_tool_progress(self.runtime.session_manager, ctx.session_id)
        )
        if (feishu_tool_progress or want_llm_stream) and not self.runtime.gateway_base_url:
            direct_run_id = str(uuid.uuid4())

        if direct_run_id is not None and feishu_tool_progress:

            async def progress_handler(event: AgentStreamEvent) -> None:
                data = event.data if isinstance(event.data, dict) else {}
                if data.get("run_id") != direct_run_id:
                    return
                line = format_agent_stream_event_for_feishu(event)
                if not line:
                    return
                fn = getattr(plugin, "feishu_send_progress", None)
                if not callable(fn):
                    return
                try:
                    await fn(line, ctx)
                except Exception as e:
                    logger.debug("feishu progress handler error: %s", e)

            es.subscribe("tool", progress_handler)

        if direct_run_id is not None and want_llm_stream:

            async def llm_handler(event: AgentStreamEvent) -> None:
                data = event.data if isinstance(event.data, dict) else {}
                if data.get("run_id") != direct_run_id:
                    return
                line = format_llm_stream_event_for_feishu(event)
                if not line:
                    return
                fn = getattr(plugin, "feishu_send_progress", None)
                if not callable(fn):
                    return
                try:
                    await fn(line, ctx)
                except Exception as e:
                    logger.debug("channel llm stream handler error: %s", e)

            es.subscribe("llm", llm_handler)

        try:
            # Call agent via Gateway RPC (aligned with OpenClaw) or direct AgentRunner
            if self.runtime.gateway_base_url:
                logger.debug("calling agent via gateway: %s", self.runtime.gateway_base_url)
                result_text = await self._call_agent_via_gateway(ctx)
            else:
                logger.debug("calling agent direct")
                result_text = await self._call_agent_direct(ctx, run_id=direct_run_id)

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
        finally:
            if progress_handler is not None:
                es.unsubscribe("tool", progress_handler)
            if llm_handler is not None:
                es.unsubscribe("llm", llm_handler)
            if typing_state is not None:
                fn_end = getattr(plugin, "feishu_typing_end", None)
                if callable(fn_end):
                    await fn_end(typing_state)
                else:
                    from .feishu_outbound import remove_typing_indicator

                    await remove_typing_indicator(typing_state)

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
            wait_ms = resolve_agent_wait_timeout_ms(None)
            wait_res = call_rpc(
                base_url=base_url,
                method="agent.wait",
                params={"runId": run_id, "timeoutMs": wait_ms},
                timeout_ms=rpc_client_timeout_ms(wait_ms),
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

    async def _call_agent_direct(self, ctx: InboundContext, *, run_id: Optional[str] = None) -> Optional[str]:
        """Call AgentRunner directly (simplified mode)."""
        params = AgentRunParams(
            message=ctx.text,
            run_id=run_id,
            session_key=ctx.session_key,
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            deliver=False,
            channel=ctx.channel,
            sender_id=ctx.sender_id,
            sender_is_owner=ctx.sender_is_owner,
            command_authorized=ctx.command_authorized,
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

