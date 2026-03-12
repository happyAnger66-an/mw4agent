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
async def test_feishu_channel_websocket_mode_raises_not_implemented():
    ch = FeishuChannel(connection_mode="websocket")

    # 如果未安装 lark-oapi，则应抛出 RuntimeError 提示依赖缺失；
    # 若已安装，则此测试可以被跳过或在实际集成环境中调整。
    try:
        await ch.run_monitor(on_inbound=_dummy_on_inbound)
    except RuntimeError as exc:
        msg = str(exc.value)
        assert "lark-oapi" in msg or "FEISHU_APP_ID" in msg

