"""Feishu channel plugin (Phase 1).

当前仅实现出站文本消息，入站 webhook/事件在后续阶段补充。

支持多应用：通过 channels.feishu.accounts 配置多个 app，每个可绑定不同 agent_id；
Gateway 启动时会为每个账号注册独立插件（channel id 为 feishu:<name>）并挂载 webhook / 启动 WS。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Optional

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from ..dock import ChannelDock
from ..feishu_accounts import FeishuAccountResolved
from ..feishu_outbound import (
    TypingIndicatorState,
    add_typing_indicator,
    remove_typing_indicator,
    send_text as send_text_outbound,
)
from ...feishu.client import FeishuConfig
from ...log import get_logger
from ..types import (
    ChannelCapabilities,
    ChannelMeta,
    InboundContext,
    OutboundPayload,
)
from .base import ChannelPlugin, InboundHandler

logger = get_logger(__name__)


@dataclass(frozen=True)
class FeishuChannel(ChannelPlugin):
    host: str = "0.0.0.0"
    port: int = 8081
    path: str = "/feishu/webhook"
    connection_mode: Literal["webhook", "websocket"] = "webhook"

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8081,
        path: str = "/feishu/webhook",
        connection_mode: Literal["webhook", "websocket"] = "webhook",
        *,
        feishu_account: FeishuAccountResolved | None = None,
    ) -> None:
        caps = ChannelCapabilities(
            chat_types=("direct", "group", "channel", "thread"),
            native_commands=True,
            block_streaming=False,
        )

        feishu_cfg: FeishuConfig | None
        if feishu_account is not None:
            plugin_id = feishu_account.plugin_channel_id
            norm_path = (
                feishu_account.webhook_path
                if feishu_account.webhook_path.startswith("/")
                else f"/{feishu_account.webhook_path}"
            )
            conn_mode = feishu_account.connection_mode  # type: ignore[assignment]
            feishu_cfg = FeishuConfig(
                app_id=feishu_account.app_id,
                app_secret=feishu_account.app_secret,
                api_base=feishu_account.api_base or "https://open.feishu.cn/open-apis",
            )
            default_agent = feishu_account.agent_id
            enc = feishu_account.encrypt_key
            vtok = feishu_account.verification_token
            acct_key = feishu_account.account_key
        else:
            plugin_id = "feishu"
            norm_path = path if path.startswith("/") else f"/{path}"
            conn_mode = connection_mode
            feishu_cfg = None
            default_agent = "main"
            enc = ""
            vtok = ""
            acct_key = "default"

        dock = ChannelDock(
            id=plugin_id,
            capabilities=caps,
            resolve_require_mention=lambda _acct: True,
        )
        meta = ChannelMeta(id=plugin_id, label=f"Feishu ({acct_key})", docs_path="/channels/feishu")

        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", int(port))
        object.__setattr__(self, "path", norm_path)
        object.__setattr__(self, "connection_mode", conn_mode)

        object.__setattr__(self, "_feishu_cfg", feishu_cfg)
        object.__setattr__(self, "_plugin_channel_id", plugin_id)
        object.__setattr__(self, "_default_agent_id", default_agent)
        object.__setattr__(self, "_encrypt_key", enc)
        object.__setattr__(self, "_verification_token", vtok)
        object.__setattr__(self, "_account_key", acct_key)

        super().__init__(id=plugin_id, meta=meta, capabilities=caps, dock=dock)

    def _session_key_for_chat(self, chat_id: Optional[str]) -> str:
        cid = chat_id or "unknown"
        if self._plugin_channel_id == "feishu":
            return f"feishu:{cid}"
        return f"{self._plugin_channel_id}:{cid}"

    def _build_inbound_context(
        self,
        *,
        text: str,
        chat_id: Optional[str],
        chat_type_raw: str,
        message_id: Optional[str],
        thread_id: Optional[str],
        sender_open_id: Optional[str],
        raw_event: Any,
    ) -> InboundContext:
        if chat_type_raw in ("p2p", "private"):
            ctx_chat_type = "direct"
        elif chat_type_raw in ("group", "supergroup"):
            ctx_chat_type = "group"
        else:
            ctx_chat_type = "channel"

        if ctx_chat_type == "group":
            was_mentioned = "@" in text or "＠" in text
        else:
            was_mentioned = True

        command_authorized = True
        session_chat_id = chat_id or "unknown"
        session_key = self._session_key_for_chat(chat_id)
        session_id = str(session_chat_id)

        return InboundContext(
            channel=self._plugin_channel_id,
            text=text,
            session_key=session_key,
            session_id=session_id,
            agent_id=self._default_agent_id,
            chat_type=ctx_chat_type,  # type: ignore[arg-type]
            was_mentioned=was_mentioned,
            command_authorized=command_authorized,
            sender_is_owner=False,
            sender_id=str(sender_open_id) if sender_open_id else None,
            sender_name=None,
            to=None,
            thread_id=str(thread_id) if thread_id else None,
            timestamp_ms=None,
            extra={
                "chat_id": chat_id,
                "message_id": message_id,
                "thread_id": thread_id,
                "sender_open_id": sender_open_id,
                "raw_event": raw_event,
                "feishu_account_key": self._account_key,
            },
        )

    async def feishu_typing_begin(self, message_id: str) -> TypingIndicatorState:
        return await add_typing_indicator(message_id, feishu_cfg=self._feishu_cfg)

    async def feishu_typing_end(self, state: TypingIndicatorState) -> None:
        await remove_typing_indicator(state, feishu_cfg=self._feishu_cfg)

    async def run_monitor(self, *, on_inbound: InboundHandler) -> None:
        """启动 Feishu 监控。

        根据 connection_mode 决定使用：
        - "webhook": HTTP 回调（当前已实现）
        - "websocket": 官方 SDK 长连接（当前仅占位，待集成 lark-oapi）
        """
        if self.connection_mode == "webhook":
            await self._run_webhook_monitor(on_inbound=on_inbound)
        elif self.connection_mode == "websocket":
            await self._run_ws_monitor(on_inbound=on_inbound)
        else:  # pragma: no cover - 防御性分支
            raise RuntimeError(f"Unsupported Feishu connection_mode: {self.connection_mode}")

    def get_webhook_router(self, on_inbound: InboundHandler) -> APIRouter:
        """返回可用于挂载到现有 FastAPI 应用的 Webhook 路由器（如随 Gateway 一起启动）。"""
        router = APIRouter(prefix=self.path.rstrip("/") or "/", tags=["feishu"])

        @router.post("")
        async def handle_feishu(req: Request):
            try:
                body: Any = await req.json()
            except Exception:
                return JSONResponse(status_code=400, content={"code": 1, "msg": "invalid_json"})

            if not isinstance(body, dict):
                return JSONResponse(status_code=400, content={"code": 1, "msg": "invalid_body"})

            # 飞书 URL 验证
            if body.get("type") == "url_verification":
                challenge = body.get("challenge")
                return JSONResponse(content={"challenge": challenge})

            event = body.get("event") or {}
            if not isinstance(event, dict):
                return JSONResponse(status_code=200, content={"code": 0, "msg": "ignored"})

            msg = event.get("message") or {}
            if not isinstance(msg, dict):
                return JSONResponse(status_code=200, content={"code": 0, "msg": "ignored"})

            message_type = msg.get("message_type") or msg.get("msg_type") or ""
            raw_content = msg.get("content") or ""
            text = ""

            if message_type == "text":
                try:
                    content_obj = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
                    if isinstance(content_obj, dict):
                        text = str(content_obj.get("text") or "").strip()
                except Exception:
                    text = str(raw_content)
            else:
                text = f"[feishu:{message_type}]"

            if not text:
                return JSONResponse(status_code=200, content={"code": 0, "msg": "empty_text"})

            chat_id = (
                msg.get("chat_id") or msg.get("chatId")
                or event.get("chat_id") or event.get("chatId")
            )
            chat_type = (
                msg.get("chat_type") or msg.get("chatType")
                or event.get("chat_type") or event.get("chatType") or "p2p"
            )
            message_id = (
                msg.get("message_id") or msg.get("messageId")
                or msg.get("msg_id") or msg.get("msgId")
            )
            thread_id = msg.get("thread_id") or msg.get("threadId") or None

            sender = event.get("sender") or {}
            sender_id_obj = sender.get("sender_id") or {}
            sender_open_id = sender_id_obj.get("open_id")

            ctx = self._build_inbound_context(
                text=text,
                chat_id=chat_id,
                chat_type_raw=str(chat_type),
                message_id=message_id,
                thread_id=thread_id,
                sender_open_id=sender_open_id,
                raw_event=body,
            )

            asyncio.create_task(on_inbound(ctx))

            return JSONResponse(content={"code": 0, "msg": "ok"})

        return router

    async def _run_webhook_monitor(self, *, on_inbound: InboundHandler) -> None:
        """启动 Feishu Webhook（事件订阅）独立监听服务。"""
        app = FastAPI(title="MW4Agent Feishu Channel")
        app.include_router(self.get_webhook_router(on_inbound))
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info")
        server = uvicorn.Server(config)
        loop = asyncio.get_running_loop()

        def _run_server() -> None:
            asyncio.run(server.serve())

        await loop.run_in_executor(None, _run_server)

    async def _run_ws_monitor(self, *, on_inbound: InboundHandler) -> None:
        """使用官方 SDK (lark-oapi) 建立 WebSocket 长连接并转发事件。"""
        try:
            import lark_oapi as lark  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 环境缺少依赖时的保护
            raise RuntimeError(
                "FeishuChannel(connection_mode='websocket') 需要安装 lark-oapi 包。\n"
                "请先运行: pip install lark-oapi"
            ) from exc

        app_id: Optional[str] = None
        app_secret: Optional[str] = None
        if self._feishu_cfg is not None:
            app_id = self._feishu_cfg.app_id
            app_secret = self._feishu_cfg.app_secret
        else:
            app_id = os.getenv("FEISHU_APP_ID")
            app_secret = os.getenv("FEISHU_APP_SECRET")
            if not app_id or not app_secret:
                try:
                    from mw4agent.config import read_root_section
                    channels = read_root_section("channels", default={})
                    feishu_cfg = channels.get("feishu") or {}
                    app_id = app_id or (feishu_cfg.get("app_id") or "").strip()
                    app_secret = app_secret or (feishu_cfg.get("app_secret") or "").strip()
                except Exception:
                    pass

        encrypt_key = self._encrypt_key or os.getenv("FEISHU_ENCRYPT_KEY", "") or ""
        verification_token = self._verification_token or os.getenv("FEISHU_VERIFICATION_TOKEN", "") or ""

        if not app_id or not app_secret:
            raise RuntimeError(
                "Feishu WebSocket 模式需要配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET（环境变量或 mw4agent configuration set-channels --channel feishu --app-id ... --app-secret ...）。"
            )

        loop = asyncio.get_running_loop()

        def _handle_im_message(data: Any) -> None:
            try:
                event = getattr(data, "event", None)
                if event is None:
                    return
                message = getattr(event, "message", None)
                if message is None:
                    return

                message_type: str = getattr(message, "message_type", "") or getattr(
                    message, "msg_type", ""
                )
                raw_content: Any = getattr(message, "content", "") or ""
                text = ""

                if message_type == "text":
                    try:
                        content_obj = (
                            json.loads(raw_content)
                            if isinstance(raw_content, str)
                            else raw_content
                        )
                        if isinstance(content_obj, dict):
                            text = str(content_obj.get("text") or "").strip()
                    except Exception:
                        text = str(raw_content)
                else:
                    text = f"[feishu:{message_type}]"

                if not text:
                    return

                chat_id: Optional[str] = getattr(message, "chat_id", None)
                chat_type: str = getattr(message, "chat_type", "p2p") or "p2p"
                message_id: Optional[str] = getattr(message, "message_id", None) or getattr(
                    message, "msg_id", None
                )
                thread_id: Optional[str] = getattr(message, "thread_id", None)

                sender = getattr(event, "sender", None)
                sender_open_id: Optional[str] = None
                if sender is not None:
                    sender_id_obj = getattr(sender, "sender_id", None)
                    if sender_id_obj is not None:
                        sender_open_id = getattr(sender_id_obj, "open_id", None)

                ctx = self._build_inbound_context(
                    text=text,
                    chat_id=chat_id,
                    chat_type_raw=str(chat_type),
                    message_id=message_id,
                    thread_id=thread_id,
                    sender_open_id=sender_open_id,
                    raw_event=lark.JSON.marshal(data),
                )

                asyncio.run_coroutine_threadsafe(on_inbound(ctx), loop)
            except Exception as e:  # pragma: no cover - 日志路径
                print(f"[Feishu WS] error handling message event: {e}")

        event_handler = (
            lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
            .register_p2_im_message_receive_v1(_handle_im_message)
            .build()
        )

        def _run_ws() -> None:
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            try:
                import lark_oapi.ws.client as ws_client_mod
                ws_client_mod.loop = ws_loop
                ws_client = lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.DEBUG,
                )
                ws_client.start()
            finally:
                ws_loop.close()

        await loop.run_in_executor(None, _run_ws)

    async def deliver(self, payload: OutboundPayload) -> None:
        """Send outbound payload to Feishu."""
        inbound = None
        if isinstance(payload.extra, dict):
            inbound = payload.extra.get("inbound")

        chat_id: str | None = None
        reply_to_id: str | None = None
        thread_id: str | None = None
        session_id: str | None = None

        if isinstance(inbound, dict):
            extra = inbound.get("extra")
            if isinstance(extra, dict):
                chat_id = extra.get("chat_id") or extra.get("chatId")
                reply_to_id = extra.get("message_id") or extra.get("messageId")
                thread_id = extra.get("thread_id") or extra.get("threadId")
            session_id = inbound.get("session_id")

        if not chat_id and session_id and str(session_id).strip() and str(session_id) != "unknown":
            chat_id = str(session_id).strip()

        if not chat_id:
            prefix = "ERR" if payload.is_error else "AI"
            logger.warning("[feishu] no chat_id (extra.session_id=%s), fallback to stdout", session_id)
            print(f"[feishu:{prefix}] {payload.text}")
            return

        logger.info("[feishu] deliver chat_id=%s reply_to=%s thread=%s", chat_id, reply_to_id, thread_id)
        try:
            await send_text_outbound(
                cfg=None,
                to=str(chat_id),
                text=payload.text,
                account_id=None,
                reply_to_id=reply_to_id,
                thread_id=thread_id,
                mentions=None,
                feishu_cfg=self._feishu_cfg,
            )
        except Exception as e:
            logger.exception("[feishu] deliver failed: %s", e)
            raise
