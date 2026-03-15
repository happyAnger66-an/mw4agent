"""Minimal Feishu/Lark Open API client for MW4Agent.

Phase 1 目标：
- 只支持 tenant_access_token 内部应用鉴权
- 只实现文本消息发送（新消息 + 回复）

配置来源（首版）：
- 环境变量：
  - FEISHU_APP_ID
  - FEISHU_APP_SECRET
  - FEISHU_API_BASE（可选，默认 https://open.feishu.cn/open-apis）

后续可以扩展为：
- 多账号支持（accounts）
- 更完整的错误分类与重试策略
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str
    api_base: str = "https://open.feishu.cn/open-apis"


class FeishuClient:
    """Very small wrapper around Feishu Open API for sending messages."""

    def __init__(self, cfg: Optional[FeishuConfig] = None) -> None:
        if cfg is None:
            app_id = _env("FEISHU_APP_ID")
            app_secret = _env("FEISHU_APP_SECRET")
            if not app_id or not app_secret:
                try:
                    from mw4agent.config import read_root_section
                    channels = read_root_section("channels", default={})
                    feishu_cfg = channels.get("feishu") or {}
                    app_id = app_id or (feishu_cfg.get("app_id") or "").strip()
                    app_secret = app_secret or (feishu_cfg.get("app_secret") or "").strip()
                except Exception:
                    pass
            if not app_id or not app_secret:
                raise RuntimeError(
                    "FeishuClient requires FEISHU_APP_ID and FEISHU_APP_SECRET "
                    "(environment variables or mw4agent configuration set-channels --channel feishu)"
                )
            api_base = _env("FEISHU_API_BASE", "https://open.feishu.cn/open-apis")
            cfg = FeishuConfig(app_id=app_id, app_secret=app_secret, api_base=api_base)
        self.cfg = cfg
        # tenant_access_token cache
        self._tenant_token: Optional[str] = None
        self._tenant_token_expire_at: float = 0.0

    @property
    def _base(self) -> str:
        return self.cfg.api_base.rstrip("/")

    async def _get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_token and now < self._tenant_token_expire_at - 60:
            return self._tenant_token

        url = f"{self._base}/auth/v3/tenant_access_token/internal"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={"app_id": self.cfg.app_id, "app_secret": self.cfg.app_secret},
            )
            resp.raise_for_status()
            data: Dict[str, Any] = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"Failed to get tenant_access_token: {data}")

        token = str(data.get("tenant_access_token") or "")
        expire = int(data.get("expire") or 0)
        if not token or expire <= 0:
            raise RuntimeError(f"Invalid tenant_access_token response: {data}")

        self._tenant_token = token
        self._tenant_token_expire_at = now + expire
        return token

    def _post_content(self, text: str) -> str:
        """Build Feishu post-format content (align with feishu-openclaw-plugin send.js)."""
        return json.dumps({
            "zh_cn": {
                "content": [[{"tag": "md", "text": text}]],
            },
        })

    async def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: Optional[str] = None,
        reply_in_thread: bool = False,
    ) -> Dict[str, Any]:
        """Send a message or reply using Feishu post format (same as OpenClaw plugin)."""
        token = await self._get_tenant_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        content_payload = self._post_content(text)

        if reply_to_message_id:
            # Reply to an existing message (same API shape as feishu-openclaw-plugin).
            url = f"{self._base}/im/v1/messages/{reply_to_message_id}/reply"
            payload = {
                "msg_type": "post",
                "content": content_payload,
                "reply_in_thread": reply_in_thread,
            }
            async with httpx.AsyncClient(timeout=10, headers=headers) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()

        # New message: use chat_id as receive_id with receive_id_type=chat_id
        url = f"{self._base}/im/v1/messages"
        params = {"receive_id_type": "chat_id"}
        payload = {
            "receive_id": chat_id,
            "msg_type": "post",
            "content": content_payload,
        }
        async with httpx.AsyncClient(timeout=10, headers=headers, params=params) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

