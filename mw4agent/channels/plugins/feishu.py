"""Feishu channel plugin (Phase 1).

当前仅实现出站文本消息，入站 webhook/事件在后续阶段补充。
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
from ..feishu_outbound import send_text as send_text_outbound
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
    ) -> None:
        caps = ChannelCapabilities(
            chat_types=("direct", "group", "channel", "thread"),
            native_commands=True,
            block_streaming=False,
        )
        dock = ChannelDock(
            id="feishu",
            capabilities=caps,
            # 群聊默认需要 @ 才触发，后续可以结合 mention 解析细化
            resolve_require_mention=lambda _acct: True,
        )
        meta = ChannelMeta(id="feishu", label="Feishu", docs_path="/channels/feishu")

        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", int(port))
        norm_path = path if path.startswith("/") else f"/{path}"
        object.__setattr__(self, "path", norm_path)
        object.__setattr__(self, "connection_mode", connection_mode)

        super().__init__(id="feishu", meta=meta, capabilities=caps, dock=dock)

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
                # content 是 JSON 字符串：{"text": "..."}
                try:
                    content_obj = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
                    if isinstance(content_obj, dict):
                        text = str(content_obj.get("text") or "").strip()
                except Exception:
                    text = str(raw_content)
            else:
                # 非文本消息，先简单转成占位文本
                text = f"[feishu:{message_type}]"

            if not text:
                return JSONResponse(status_code=200, content={"code": 0, "msg": "empty_text"})

            # 与 feishu-openclaw-plugin 一致：event.message.chat_id / message_id；兼容 snake_case / camelCase
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

            # 映射 chat_type 到 InboundContext.chat_type
            if chat_type in ("p2p", "private"):
                ctx_chat_type = "direct"
            elif chat_type in ("group", "supergroup"):
                ctx_chat_type = "group"
            else:
                ctx_chat_type = "channel"

            # mention & command 简易解析：
            # - 直聊：默认视为已提及
            # - 群聊：如果文本中出现 "@" 或 "＠" 字符，则视为已提及；否则认为未提及
            if ctx_chat_type == "group":
                was_mentioned = "@" in text or "＠" in text
            else:
                was_mentioned = True

            # Phase 3 先不做细粒度指令/权限控制，全部视为已授权
            command_authorized = True

            session_chat_id = chat_id or "unknown"
            session_key = f"feishu:{session_chat_id}"
            session_id = str(session_chat_id)

            ctx = InboundContext(
                channel="feishu",
                text=text,
                session_key=session_key,
                session_id=session_id,
                agent_id="main",
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
                    "raw_event": body,
                },
            )

            # 不阻塞 HTTP 请求，将处理交给 dispatcher
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
        """使用官方 SDK (lark-oapi) 建立 WebSocket 长连接并转发事件。

        要求环境变量：
        - FEISHU_APP_ID
        - FEISHU_APP_SECRET
        - FEISHU_ENCRYPT_KEY（若未加密，可留空）
        - FEISHU_VERIFICATION_TOKEN
        """
        try:
            import lark_oapi as lark  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 环境缺少依赖时的保护
            raise RuntimeError(
                "FeishuChannel(connection_mode='websocket') 需要安装 lark-oapi 包。\n"
                "请先运行: pip install lark-oapi"
            ) from exc

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
        encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "") or ""
        verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "") or ""

        if not app_id or not app_secret:
            raise RuntimeError(
                "Feishu WebSocket 模式需要配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET（环境变量或 mw4agent configuration set-channels --channel feishu --app-id ... --app-secret ...）。"
            )

        loop = asyncio.get_running_loop()

        def _handle_im_message(data: Any) -> None:
            """处理 P2ImMessageReceiveV1 事件，转换为 InboundContext."""
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

                # 映射 chat_type 到 InboundContext.chat_type
                if chat_type in ("p2p", "private"):
                    ctx_chat_type = "direct"
                elif chat_type in ("group", "supergroup"):
                    ctx_chat_type = "group"
                else:
                    ctx_chat_type = "channel"

                if ctx_chat_type == "group":
                    was_mentioned = "@" in text or "＠" in text
                else:
                    was_mentioned = True

                command_authorized = True

                session_chat_id = chat_id or "unknown"
                session_key = f"feishu:{session_chat_id}"
                session_id = str(session_chat_id)

                ctx = InboundContext(
                    channel="feishu",
                    text=text,
                    session_key=session_key,
                    session_id=session_id,
                    agent_id="main",
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
                        "raw_event": lark.JSON.marshal(data),
                    },
                )

                asyncio.run_coroutine_threadsafe(on_inbound(ctx), loop)
            except Exception as e:  # pragma: no cover - 日志路径
                print(f"[Feishu WS] error handling message event: {e}")

        # 构建事件分发器（_handle_im_message 内用 run_coroutine_threadsafe 回主 loop，无需改）
        event_handler = (
            lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
            .register_p2_im_message_receive_v1(_handle_im_message)
            .build()
        )

        # lark-oapi 在模块加载时固定了全局 loop，且 Client.start() 里用 loop.run_until_complete()。
        # 若在 run_in_executor 的线程里直接 start()，用的仍是主线程已运行的 loop → RuntimeError: this event loop is already running.
        # 做法：在 WS 线程内新建并设置该线程的 event loop，并让 lark_oapi.ws.client 使用该 loop，且在该线程内创建 Client（Lock 等绑定到该 loop），再 start()。
        def _run_ws() -> None:
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            try:
                import lark_oapi.ws.client as ws_client_mod
                ws_client_mod.loop = ws_loop  # 使 Client.start() 使用本线程的 loop
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
        """Send outbound payload to Feishu.

        Phase 1: 仅支持文本消息。chat_id 优先从 inbound.extra 取，缺省时用 inbound.session_id（feishu 下即会话 chat_id）回退。
        """
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

        # 与 feishu-openclaw-plugin 一致：session 即 chat_id 时可用作回退
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
            )
        except Exception as e:
            logger.exception("[feishu] deliver failed: %s", e)
            raise

