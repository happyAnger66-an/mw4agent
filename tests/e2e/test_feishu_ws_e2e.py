import asyncio
import json
import sys
import types

import pytest

from mw4agent.channels.plugins.feishu import FeishuChannel
from mw4agent.channels.types import InboundContext


@pytest.mark.asyncio
async def test_feishu_ws_e2e_monkeypatched_lark_oapi(monkeypatch):
    """E2E-style test: simulate Feishu WS event via mocked lark_oapi."""

    events: list[InboundContext] = []

    async def on_inbound(ctx: InboundContext) -> None:
        events.append(ctx)

    # ---- 构造 fake lark_oapi 模块 ----
    fake_lark = types.SimpleNamespace()

    class FakeEvent:
        """模拟 P2ImMessageReceiveV1 数据结构."""

        def __init__(self) -> None:
            message = types.SimpleNamespace(
                message_type="text",
                content=json.dumps({"text": "hello ws"}),
                message_id="om_ws",
                chat_id="oc_ws",
                chat_type="p2p",
                thread_id=None,
            )
            sender = types.SimpleNamespace(
                sender_id=types.SimpleNamespace(open_id="ou_ws")
            )
            self.event = types.SimpleNamespace(message=message, sender=sender)

    class FakeJSON:
        @staticmethod
        def marshal(obj, indent: int = 4) -> str:
            # 仅用于调试输出，这里返回一个固定 JSON 即可
            return json.dumps({"mock": True}, indent=indent)

    class FakeHandlerBuilder:
        def __init__(self) -> None:
            self._cb = None

        def register_p2_im_message_receive_v1(self, cb):
            self._cb = cb
            return self

        def build(self):
            # 返回一个简单对象，持有回调
            return types.SimpleNamespace(_cb=self._cb)

    class FakeEventDispatcherHandler:
        @staticmethod
        def builder(_encrypt_key: str, _verification_token: str):
            return FakeHandlerBuilder()

    class FakeWSClient:
        def __init__(self, app_id, app_secret, event_handler, log_level, app_type):
            self._handler = event_handler

        def start(self) -> None:
            # 直接触发一次事件回调后返回，模拟一次 WS 消息接收
            self._handler._cb(FakeEvent())

    fake_lark.EventDispatcherHandler = FakeEventDispatcherHandler
    fake_lark.JSON = FakeJSON
    fake_lark.ws = types.SimpleNamespace(Client=FakeWSClient)
    fake_lark.LogLevel = types.SimpleNamespace(DEBUG="DEBUG")
    fake_lark.AppType = types.SimpleNamespace(SELF_BUILD="SELF_BUILD")

    # ---- 注入 fake 模块与环境变量 ----
    monkeypatch.setenv("FEISHU_APP_ID", "test_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test_app_secret")
    monkeypatch.setenv("FEISHU_ENCRYPT_KEY", "")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "")
    monkeypatch.setitem(sys.modules, "lark_oapi", fake_lark)

    ch = FeishuChannel(connection_mode="websocket")

    # run_monitor 会调用 _run_ws_monitor -> FakeWSClient.start -> 触发回调
    await ch.run_monitor(on_inbound=on_inbound)

    # 验证我们确实收到了一个 InboundContext，并且字段被正确映射
    assert len(events) == 1
    ctx = events[0]
    assert ctx.channel == "feishu"
    assert ctx.text == "hello ws"
    assert ctx.session_key == "feishu:oc_ws"
    assert ctx.session_id == "oc_ws"
    assert ctx.chat_type == "direct"
    assert ctx.sender_id == "ou_ws"

