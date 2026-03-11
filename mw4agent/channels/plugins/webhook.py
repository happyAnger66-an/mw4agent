"""Generic HTTP webhook channel plugin.

目标：
- 提供一个最小的 HTTP 入站通道，将外部系统的 webhook 请求转换为 InboundContext
- 出站先简单打印到 stdout，后续可以扩展为回调 URL 或队列投递

实现：
- 内部启动一个 FastAPI + Uvicorn 服务器
- 暴露 POST /webhook（路径可配置），body 约定：
  {
    "text": "用户消息",
    "sessionKey": "可选，默认 webhook:default",
    "sessionId": "可选，默认 default",
    "agentId": "可选，默认 main"
  }
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse

from ..dock import ChannelDock
from ..types import (
    ChannelCapabilities,
    ChannelMeta,
    InboundContext,
    OutboundPayload,
)
from .base import ChannelPlugin, InboundHandler


@dataclass(frozen=True)
class WebhookChannel(ChannelPlugin):
    host: str = "0.0.0.0"
    port: int = 8080
    path: str = "/webhook"

    def __init__(self, host: str = "0.0.0.0", port: int = 8080, path: str = "/webhook") -> None:
        caps = ChannelCapabilities(
            chat_types=("direct",),
            native_commands=False,
            block_streaming=False,
        )
        dock = ChannelDock(
            id="webhook",
            capabilities=caps,
            # Webhook 通道通常由后端系统调用，不做 mention 要求
            resolve_require_mention=lambda _acct: False,
        )
        meta = ChannelMeta(id="webhook", label="Webhook", docs_path="/channels/webhook")

        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", int(port))
        # 确保 path 以 / 开头
        norm_path = path if path.startswith("/") else f"/{path}"
        object.__setattr__(self, "path", norm_path)

        super().__init__(id="webhook", meta=meta, capabilities=caps, dock=dock)

    async def run_monitor(self, *, on_inbound: InboundHandler) -> None:
        """启动一个 FastAPI + Uvicorn 进程，接收 HTTP webhook。"""

        app = FastAPI(title="MW4Agent Webhook Channel")

        @app.post(self.path)
        async def handle_webhook(req: Request):
            try:
                body: Any = await req.json()
            except Exception:
                return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_json"})

            if not isinstance(body, dict):
                return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_body"})

            raw_text = body.get("text")
            if not isinstance(raw_text, str) or not raw_text.strip():
                return JSONResponse(status_code=400, content={"ok": False, "error": "text_required"})

            text = raw_text.strip()

            session_key = str(body.get("sessionKey") or "webhook:default")
            session_id = str(body.get("sessionId") or "default")
            agent_id = str(body.get("agentId") or "main")

            ctx = InboundContext(
                channel="webhook",
                text=text,
                session_key=session_key,
                session_id=session_id,
                agent_id=agent_id,
                chat_type="direct",
                was_mentioned=True,
                command_authorized=True,
                sender_is_owner=True,
                sender_id=None,
                sender_name=None,
                to=None,
                thread_id=None,
                timestamp_ms=None,
                extra={"raw_body": body},
            )

            # 这里不能直接 await on_inbound(ctx)，否则会阻塞 HTTP 请求；
            # 使用 create_task 在后台执行，当前请求只返回“已接受”。
            asyncio.create_task(on_inbound(ctx))

            return {"ok": True}

        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info")
        server = uvicorn.Server(config)

        # Uvicorn Server 的 run() 是同步阻塞的，这里通过线程池在后台运行。
        loop = asyncio.get_running_loop()

        def _run_server() -> None:
            asyncio.run(server.serve())

        await loop.run_in_executor(None, _run_server)

    async def deliver(self, payload: OutboundPayload) -> None:
        """当前版本中，Webhook 通道仅将出站内容打印到 stdout。"""
        prefix = "ERR" if payload.is_error else "AI"
        print(f"[webhook:{prefix}] {payload.text}")

