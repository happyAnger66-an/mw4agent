import asyncio

import pytest

from mw4agent.channels.plugins.feishu import FeishuChannel
from mw4agent.channels.types import InboundContext


async def _dummy_on_inbound(_ctx: InboundContext) -> None:
    # 用于 run_monitor 的占位回调
    return None


@pytest.mark.asyncio
async def test_feishu_channel_defaults_to_webhook_mode():
    ch = FeishuChannel()
    assert ch.connection_mode == "webhook"


@pytest.mark.asyncio
async def test_feishu_channel_websocket_mode_raises_not_implemented(monkeypatch):
    # 强制“无凭证”路径，避免在已安装 lark-oapi 且配置了凭证时进入真实 WS 连接导致挂死。
    monkeypatch.setenv("FEISHU_APP_ID", "")
    monkeypatch.setenv("FEISHU_APP_SECRET", "")
    def _no_channels_config(section, default=None):
        return {} if section == "channels" else (default or {})

    monkeypatch.setattr("mw4agent.config.read_root_section", _no_channels_config)
    ch = FeishuChannel(connection_mode="websocket")

    # 未安装 lark-oapi 时抛出“需安装 lark-oapi”；无凭证时抛出“需配置 FEISHU_APP_ID/APP_SECRET”。
    with pytest.raises(RuntimeError) as exc_info:
        await ch.run_monitor(on_inbound=_dummy_on_inbound)
    msg = str(exc_info.value)
    assert "lark-oapi" in msg or "FEISHU_APP_ID" in msg

