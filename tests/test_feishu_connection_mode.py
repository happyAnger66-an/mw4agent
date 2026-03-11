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

    with pytest.raises(RuntimeError) as exc:
        await ch.run_monitor(on_inbound=_dummy_on_inbound)

    msg = str(exc.value)
    assert "connection_mode='websocket'" in msg
    assert "尚未在 mw4agent 中实现" in msg

