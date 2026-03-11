"""Feishu outbound adapter for MW4Agent.

Phase 1: minimal text-only implementation.
"""

from __future__ import annotations

from typing import Optional

from ..feishu.client import FeishuClient


async def send_text(
    *,
    cfg: object | None,
    to: str,
    text: str,
    account_id: Optional[str] = None,
    reply_to_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    mentions: Optional[list[str]] = None,
) -> None:
    """Send a text message to Feishu.

    - `to` 目前视为 chat_id
    - Phase 1 中忽略 cfg/account_id 等多账号细节，由环境变量控制凭证
    """
    message_text = text
    # 简单 mention 拼接（后续可对齐 OpenClaw 的 @ 语法）
    if mentions:
        prefix = " ".join(mentions)
        if prefix:
            message_text = f"{prefix} {message_text}"

    client = FeishuClient()
    await client.send_text(
        chat_id=to,
        text=message_text,
        reply_to_message_id=reply_to_id,
        reply_in_thread=bool(thread_id),
    )

