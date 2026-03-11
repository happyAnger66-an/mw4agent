"""Telegram channel plugin.

实现基于 Telegram Bot API 的简单通道插件：
- 入站：通过 long polling 调用 getUpdates，将消息映射为 InboundContext
- 出站：通过 sendMessage 把 OutboundPayload 发送回对应 chat

依赖：
- 需要 TELEGRAM_BOT_TOKEN（或在 __init__ 参数中显式传入）
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Iterable, Optional

import httpx

from ..dock import ChannelDock
from ..types import (
    ChannelCapabilities,
    ChannelMeta,
    InboundContext,
    OutboundPayload,
)
from .base import ChannelPlugin, InboundHandler


@dataclass(frozen=True)
class TelegramChannel(ChannelPlugin):
    bot_token: str
    api_base: str = "https://api.telegram.org"
    long_poll_timeout: int = 25

    def __init__(
        self,
        bot_token: Optional[str] = None,
        *,
        api_base: str = "https://api.telegram.org",
        long_poll_timeout: int = 25,
    ) -> None:
        token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("Telegram bot token required (env TELEGRAM_BOT_TOKEN or bot_token arg)")

        caps = ChannelCapabilities(
            chat_types=("direct", "group", "channel", "thread"),
            native_commands=True,
            block_streaming=False,
        )
        dock = ChannelDock(
            id="telegram",
            capabilities=caps,
            # 群聊默认需要 @ 才触发（后续可基于配置调整）
            resolve_require_mention=lambda _acct: True,
        )
        meta = ChannelMeta(id="telegram", label="Telegram", docs_path="/channels/telegram")

        object.__setattr__(self, "bot_token", token)
        object.__setattr__(self, "api_base", api_base.rstrip("/"))
        object.__setattr__(self, "long_poll_timeout", int(long_poll_timeout))

        super().__init__(id="telegram", meta=meta, capabilities=caps, dock=dock)

    # --- Telegram Bot API helpers -------------------------------------------------

    @property
    def _bot_base(self) -> str:
        return f"{self.api_base}/bot{self.bot_token}"

    async def _get_updates(self, client: httpx.AsyncClient, offset: Optional[int]) -> Iterable[dict]:
        params = {
            "timeout": self.long_poll_timeout,
        }
        if offset is not None:
            params["offset"] = offset
        resp = await client.get(f"{self._bot_base}/getUpdates", params=params)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data.get("ok"):
            return []
        results = data.get("result") or []
        if not isinstance(results, list):
            return []
        return results

    async def _send_message(self, client: httpx.AsyncClient, chat_id: int | str, text: str) -> None:
        payload = {"chat_id": chat_id, "text": text}
        try:
            await client.post(f"{self._bot_base}/sendMessage", json=payload, timeout=20)
        except Exception:
            # 出站失败在当前版本仅做吞掉，避免影响主流程
            return

    # --- ChannelPlugin interface --------------------------------------------------

    async def run_monitor(self, *, on_inbound: InboundHandler) -> None:
        """使用 long polling 从 Telegram 拉取消息并转成 InboundContext。"""
        offset: Optional[int] = None

        async with httpx.AsyncClient(timeout=None) as client:
            while True:
                try:
                    updates = await self._get_updates(client, offset)
                except Exception:
                    # 短暂退避，避免在网络错误时刷满日志
                    await asyncio.sleep(5)
                    continue

                for update in updates:
                    try:
                        update_id = update.get("update_id")
                        if isinstance(update_id, int):
                            offset = update_id + 1

                        message = update.get("message") or update.get("edited_message")
                        if not isinstance(message, dict):
                            continue

                        text = message.get("text")
                        if not isinstance(text, str) or not text.strip():
                            continue

                        chat = message.get("chat") or {}
                        if not isinstance(chat, dict):
                            continue

                        chat_id = chat.get("id")
                        chat_type = str(chat.get("type") or "private")

                        if chat_type in ("group", "supergroup"):
                            resolved_chat_type = "group"
                        elif chat_type == "channel":
                            resolved_chat_type = "channel"
                        else:
                            resolved_chat_type = "direct"

                        from_user = message.get("from") or {}
                        sender_id = from_user.get("id")
                        sender_name = (from_user.get("username") or from_user.get("first_name") or "") or None

                        session_key = f"telegram:{chat_id}"
                        session_id = str(chat_id)

                        ctx = InboundContext(
                            channel="telegram",
                            text=text,
                            session_key=session_key,
                            session_id=session_id,
                            agent_id="main",
                            chat_type=resolved_chat_type,  # type: ignore[arg-type]
                            was_mentioned=True,  # 首版先视为已提及，后续可解析 @
                            command_authorized=True,
                            sender_is_owner=False,
                            sender_id=str(sender_id) if sender_id is not None else None,
                            sender_name=sender_name,
                            to=None,
                            thread_id=None,
                            timestamp_ms=None,
                            extra={"chat_id": chat_id, "raw_update": update},
                        )

                        await on_inbound(ctx)
                    except Exception:
                        # 单条 update 失败不影响整体循环
                        continue

    async def deliver(self, payload: OutboundPayload) -> None:
        """根据 OutboundPayload.extra.inbound 信息，把消息发回对应 chat。"""
        inbound = (payload.extra or {}).get("inbound") if isinstance(payload.extra, dict) else None  # type: ignore[attr-defined]
        chat_id = None
        if isinstance(inbound, dict):
            channel_extra = inbound.get("extra")
            if isinstance(channel_extra, dict):
                chat_id = channel_extra.get("chat_id")

        if chat_id is None:
            # 没有 chat_id 时退化为标准输出，至少有可见结果
            prefix = "ERR" if payload.is_error else "AI"
            print(f"[telegram:{prefix}] {payload.text}")
            return

        async with httpx.AsyncClient(timeout=20) as client:
            await self._send_message(client, chat_id=chat_id, text=payload.text)

