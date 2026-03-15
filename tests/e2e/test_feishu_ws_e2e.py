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
    # 插件内会执行 import lark_oapi.ws.client，因此 lark_oapi 必须是“包”（有 __path__），
    # 且需预注册 lark_oapi.ws 与 lark_oapi.ws.client，否则子线程里导入会报 "not a package"。
    fake_lark = types.ModuleType("lark_oapi")
    fake_lark.__path__ = []  # 使其被当作 package

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
        def __init__(self, app_id, app_secret, event_handler, log_level, app_type=None):
            self._handler = event_handler

        def start(self) -> None:
            # 直接触发一次事件回调后返回，模拟一次 WS 消息接收
            self._handler._cb(FakeEvent())

    fake_lark.EventDispatcherHandler = FakeEventDispatcherHandler
    fake_lark.JSON = FakeJSON
    fake_lark.LogLevel = types.SimpleNamespace(DEBUG="DEBUG")
    fake_lark.AppType = types.SimpleNamespace(SELF_BUILD="SELF_BUILD")

    # 子包 lark_oapi.ws 与 lark_oapi.ws.client（_run_ws 线程内会 import lark_oapi.ws.client）
    fake_ws = types.ModuleType("lark_oapi.ws")
    fake_ws.Client = FakeWSClient
    fake_lark.ws = fake_ws

    fake_ws_client = types.ModuleType("lark_oapi.ws.client")
    fake_ws_client.loop = None  # 插件会设置 ws_client_mod.loop = ws_loop

    # ---- 注入 fake 模块与环境变量 ----
    monkeypatch.setenv("FEISHU_APP_ID", "test_app_id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test_app_secret")
    monkeypatch.setenv("FEISHU_ENCRYPT_KEY", "")
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "")
    monkeypatch.setitem(sys.modules, "lark_oapi", fake_lark)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws", fake_ws)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws.client", fake_ws_client)

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

