"""在飞书会话中发起用户 OAuth（设备码流 + 消息卡片），对齐 OpenClaw 推卡片体验。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, Optional
from urllib.parse import quote

from mw4agent.feishu.client import FeishuClient, FeishuConfig
from mw4agent.log import get_logger
from mw4agent.feishu.user_oauth import (
    DEFAULT_DOC_OAUTH_SCOPES,
    poll_device_token,
    request_device_authorization,
    save_user_token_for_app,
)

logger = get_logger(__name__)

_OAUTH_CMD = re.compile(r"^/(mw4auth|feishu_auth|feishuauth)\s*$", re.IGNORECASE)
_OAUTH_PHRASES = frozenset({"飞书授权", "文档授权"})


def is_feishu_oauth_chat_command(text: str) -> bool:
    t = (text or "").strip()
    if _OAUTH_CMD.match(t):
        return True
    return t in _OAUTH_PHRASES


def to_in_app_web_url(target_url: str) -> str:
    """飞书客户端内打开网页（与 feishu-openclaw-plugin oauth.js 一致）。"""
    enc = quote(target_url, safe="")
    lk_meta = quote(
        json.dumps({"page-meta": {"showNavBar": "false", "showBottomNavBar": "false"}}),
        safe="",
    )
    return (
        "https://applink.feishu.cn/client/web_url/open"
        f"?mode=sidebar-semi&max_width=800&reload=false&url={enc}&lk_meta={lk_meta}"
    )


def build_feishu_oauth_card(
    *,
    verification_uri_complete: str,
    user_code: str,
    expires_min: int,
    scope_hint: str = "",
) -> Dict[str, Any]:
    in_app = to_in_app_web_url(verification_uri_complete)
    multi_url = {
        "url": in_app,
        "pc_url": in_app,
        "android_url": in_app,
        "ios_url": in_app,
    }
    scope_line = (
        f"\n\n所需权限（节选）：`{scope_hint[:200]}{'…' if len(scope_hint) > 200 else ''}`"
        if scope_hint
        else ""
    )
    md = (
        "请完成 **用户授权**，以便以你的身份访问云文档（MCP）。\n\n"
        f"**用户码**：`{user_code}`（若网页要求输入）"
        f"{scope_line}"
    )
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "需要你的授权"},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": "lock-chat_filled"},
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": md, "text_size": "normal"},
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_align": "right",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "auto",
                            "elements": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "前往授权"},
                                    "type": "primary",
                                    "size": "medium",
                                    "multi_url": multi_url,
                                }
                            ],
                        }
                    ],
                },
                {
                    "tag": "markdown",
                    "content": f"<font color='grey'>链接约 {expires_min} 分钟内有效；完成后本对话会收到确认。</font>",
                    "text_size": "notation",
                },
            ]
        },
    }


async def run_feishu_oauth_card_flow(
    *,
    feishu_cfg: FeishuConfig,
    chat_id: str,
    sender_open_id: str,
    brand: str = "feishu",
    scope: Optional[str] = None,
) -> None:
    """请求 device code → 发卡片 → 后台轮询 → 按发送者 open_id 落盘 UAT。"""
    if not (chat_id or "").strip():
        logger.warning("feishu_oauth_chat: missing chat_id")
        return
    app_id = feishu_cfg.app_id
    app_secret = feishu_cfg.app_secret
    user_key = (sender_open_id or "").strip() or None

    try:
        dev = request_device_authorization(
            app_id=app_id,
            app_secret=app_secret,
            brand=brand,
            scope=scope,
        )
    except Exception as e:
        logger.exception("feishu_oauth_chat: device_authorization failed: %s", e)
        client = FeishuClient(feishu_cfg)
        try:
            await client.send_text(
                chat_id=chat_id,
                text=f"无法发起授权：{e}",
            )
        except Exception:
            pass
        return

    expires_min = max(1, int(dev.get("expires_in") or 240) // 60)
    card = build_feishu_oauth_card(
        verification_uri_complete=str(
            dev.get("verification_uri_complete") or dev.get("verification_uri") or ""
        ),
        user_code=str(dev.get("user_code") or ""),
        expires_min=expires_min,
        scope_hint=scope or DEFAULT_DOC_OAUTH_SCOPES,
    )
    client = FeishuClient(feishu_cfg)
    try:
        await client.send_interactive_card(chat_id=chat_id, card=card)
    except Exception as e:
        logger.exception("feishu_oauth_chat: send card failed: %s", e)
        await client.send_text(
            chat_id=chat_id,
            text=(
                "授权链接（若卡片发送失败请手动打开）：\n"
                f"{dev.get('verification_uri_complete') or dev.get('verification_uri')}\n"
                f"用户码：{dev.get('user_code')}"
            ),
        )

    device_code = str(dev.get("device_code") or "")
    expires_in = int(dev.get("expires_in") or 240)
    interval = int(dev.get("interval") or 5)

    async def _poll_and_finish() -> None:
        loop = asyncio.get_running_loop()

        def _poll() -> Dict[str, Any]:
            return poll_device_token(
                app_id=app_id,
                app_secret=app_secret,
                device_code=device_code,
                expires_in=expires_in,
                interval=interval,
                brand=brand,
            )

        result = await loop.run_in_executor(None, _poll)
        c2 = FeishuClient(feishu_cfg)
        if result.get("ok"):
            try:
                save_user_token_for_app(
                    app_id,
                    access_token=result["access_token"],
                    refresh_token=result.get("refresh_token") or "",
                    expires_in=int(result.get("expires_in") or 7200),
                    scope=str(result.get("scope") or ""),
                    user_open_id=user_key,
                )
                await c2.send_text(chat_id=chat_id, text="✅ 授权成功，已保存用户令牌，可继续使用文档相关能力。")
            except Exception as ex:
                logger.exception("feishu_oauth_chat: save or notify failed: %s", ex)
                try:
                    await c2.send_text(chat_id=chat_id, text=f"授权已成功但保存失败：{ex}")
                except Exception:
                    pass
        else:
            msg = result.get("message") or result.get("error") or "授权未完成"
            try:
                await c2.send_text(chat_id=chat_id, text=f"授权未成功：{msg}")
            except Exception:
                pass

    asyncio.create_task(_poll_and_finish())
