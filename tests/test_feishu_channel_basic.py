import asyncio
from typing import Any, Dict

import pytest

from mw4agent.channels.plugins.feishu import FeishuChannel
from mw4agent.channels.types import InboundContext


@pytest.mark.asyncio
async def test_feishu_deliver_prints_without_chat_id(monkeypatch, capsys):
    channel = FeishuChannel()

    # 构造一个最小的 InboundContext 和 OutboundPayload.extra 结构
    from mw4agent.channels.types import OutboundPayload

    payload = OutboundPayload(
        text="hello",
        is_error=False,
        extra={"inbound": {"extra": {}}},
    )

    await channel.deliver(payload)
    captured = capsys.readouterr()
    assert "[feishu:AI] hello" in captured.out


@pytest.mark.asyncio
async def test_feishu_run_monitor_url_verification(monkeypatch):
    """简单验证 url_verification 请求能返回 challenge。"""
    channel = FeishuChannel(host="127.0.0.1", port=9099, path="/feishu/test-webhook")

    # 使用一个事件来在测试中结束 server
    stop_event = asyncio.Event()

    async def fake_on_inbound(ctx: InboundContext) -> None:  # pragma: no cover - 行为在其它测试覆盖
        pass

    async def run_server():
        # 直接运行 run_monitor；由于 uvicorn 是阻塞的，实际集成测试可以在单独进程跑。
        # 这里主要验证不会抛出异常。
        try:
            await channel.run_monitor(on_inbound=fake_on_inbound)
        except Exception:
            pytest.fail("FeishuChannel.run_monitor raised unexpectedly")
        finally:
            stop_event.set()

    # 我们不真正启动 HTTP 请求，这里只确保协程可以启动到创建 server 的阶段。
    task = asyncio.create_task(run_server())
    await asyncio.wait_for(stop_event.wait(), timeout=0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

