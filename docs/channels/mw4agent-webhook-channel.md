# MW4Agent Webhook 通道架构与实现

本文档说明 MW4Agent 中通用 Webhook 通道的架构设计与实现方式，目标是为各种后端/第三方系统提供一个简单、统一的 HTTP 入站接入点。

## 1. 目标与能力边界

- **目标**
  - 提供一个轻量的 HTTP 入口，将外部系统的请求转换成 `InboundContext`；
  - 复用 `ChannelDispatcher` → `AgentRunner` → 工具/LLM 流程；
  - 便于后续扩展成 Slack/自研系统等的“桥接层”。

- **当前能力边界**
  - 仅实现 **入站 Webhook → Agent** 的链路；
  - 出站目前只是将回复打印到 stdout（`[webhook:AI] ...`），未实现回调 URL；
  - 每个 HTTP 请求独立触发一次 agent 运行，调用方只得到“已接受（accepted）”结果。

## 2. 文件与依赖

- 代码文件：
  - `mw4agent/channels/plugins/webhook.py`
- 依赖：
  - 复用项目中已有的：
    - `fastapi`
    - `uvicorn`

## 3. 架构概览

整体链路如下：

```text
HTTP POST /webhook (FastAPI)
  ↓ 解析 body -> InboundContext
WebhookChannel (ChannelPlugin)
  ↓
ChannelDispatcher.dispatch_inbound()
  ↓
AgentRunner.run() → LLM/tools/events
  ↓
OutboundPayload  →  WebhookChannel.deliver() → stdout
```

### 3.1 ChannelPlugin：`WebhookChannel`

文件：`mw4agent/channels/plugins/webhook.py`

- 继承自 `ChannelPlugin`，定义：
  - `id = "webhook"`
  - `meta = ChannelMeta(id="webhook", label="Webhook", docs_path="/channels/webhook")`
  - `capabilities = ChannelCapabilities(chat_types=("direct",), native_commands=False, block_streaming=False)`
  - `dock = ChannelDock(id="webhook", capabilities=..., resolve_require_mention=lambda _acct: False)`
    - Webhook 一般由服务端调用，不做 mention 要求。

- 额外字段：
  - `host: str = "0.0.0.0"`
  - `port: int = 8080`
  - `path: str = "/webhook"`

这些参数都可以通过 CLI 传入，用于控制 HTTP 服务的监听配置。

### 3.2 FastAPI + Uvicorn 服务器

`WebhookChannel.run_monitor(on_inbound)` 内部会：

1. 创建 `FastAPI` 应用，注册一个 POST 路由：

   - 路径为 `self.path`（默认 `/webhook`）
   - Body 约定为 JSON：
     ```json
     {
       "text": "用户消息",
       "sessionKey": "可选，默认 webhook:default",
       "sessionId": "可选，默认 default",
       "agentId": "可选，默认 main"
     }
     ```

2. 在 handler 中：

   - 校验 JSON 结构及 `text` 字段；
   - 构造 `InboundContext`：
     - `channel="webhook"`
     - `text=body["text"]`
     - `session_key=sessionKey or "webhook:default"`
     - `session_id=sessionId or "default"`
     - `agent_id=agentId or "main"`
     - 其它 gating 字段设为默认“允许”：
       - `chat_type="direct"`
       - `was_mentioned=True`
       - `command_authorized=True`
       - `sender_is_owner=True`
     - `extra={"raw_body": body}`

   - 使用 `asyncio.create_task(on_inbound(ctx))` 将处理逻辑丢到后台，不阻塞当前 HTTP 请求；
   - 立即返回 `{"ok": True}` 给调用方。

3. 通过 `uvicorn.Server` 启动 HTTP 服务：

   - 配置：`uvicorn.Config(app, host=self.host, port=self.port, log_level="info")`
   - 由于 `server.serve()` 是同步阻塞调用，使用 `loop.run_in_executor(None, _run_server)` 的方式在后台线程中运行。

### 3.3 出站行为

当前版本中，Webhook 通道的 `deliver` 实现非常简单：

```python
async def deliver(self, payload: OutboundPayload) -> None:
    prefix = "ERR" if payload.is_error else "AI"
    print(f"[webhook:{prefix}] {payload.text}")
```

即：

- 所有 Agent 回复都会打印到 stdout；
- 没有自动回调到 HTTP 调用方；
- 为将来扩展“回调 URL / 事件总线”预留了空间。

如果需要更强的集成方式，可以在 `payload.extra["inbound"]` 中携带回调 URL 等信息，并在 `deliver` 中完成 HTTP 回调。

## 4. 与 Dispatcher 的协作

与其它通道一样，WebhookChannel 通过 `on_inbound` 与 `ChannelDispatcher` 对接：

- 入站：
  - `run_monitor(on_inbound)` 在收到 HTTP 请求后构造 `InboundContext`；
  - 调用 `on_inbound(ctx)` → 进入统一的 dispatch 流程。

- 出站：
  - `ChannelDispatcher.dispatch_inbound` 在调用 `AgentRunner.run()` 后，会把入站上下文打包到 `OutboundPayload.extra["inbound"]` 中；
  - Webhook 通道当前版本没有利用这些信息，仅做 stdout 打印；
  - 将来可以基于 `inbound.extra` 中的字段，实现 per-request 的回调逻辑。

## 5. CLI 接入方式

文件：`mw4agent/cli/channels/register.py`

- 命令层级：

```bash
mw4agent channels webhook run \
  --session-file mw4agent.sessions.json \
  --host 0.0.0.0 \
  --port 8080 \
  --path /webhook
```

- 入口实现（简化描述）：
  - `get_channel_registry().register_plugin(WebhookChannel(host=host, port=port, path=path))`
  - 构造：
    - `SessionManager(session_file)`
    - `AgentRunner(session_manager)`
    - `ChannelDispatcher(ChannelRuntime(session_manager, agent_runner))`
  - 调用 `dispatcher.run_channel("webhook")`，从而启动 Webhook HTTP 服务。

## 6. 典型请求示例

调用示例（curl）：

```bash
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello from webhook",
    "sessionKey": "webhook:demo",
    "sessionId": "demo",
    "agentId": "main"
  }'
```

行为：

1. Webhook 通道收到请求，构造 `InboundContext` 并交给 `ChannelDispatcher`；
2. Dispatcher 调用 `AgentRunner.run()`，触发一轮智能体执行；
3. 执行完成后，`WebhookChannel.deliver` 在 stdout 打印类似：
   - `[webhook:AI] <agent reply text>`

## 7. 后续扩展建议

- **出站回调**：
  - 在入站 body 中增加 `callbackUrl` 字段；
  - 在 `deliver` 中解析 `payload.extra["inbound"]["extra"]["raw_body"]["callbackUrl"]` 并执行 HTTP POST 回调。

- **认证与签名校验**：
  - 在 FastAPI 路由中加入签名校验（如 HMAC header）；
  - 在 `extra` 中记录验证结果，并与权限系统联动。

- **多租户与路由策略**：
  - 使用 `sessionKey`/`sessionId` 编码租户/用户信息；
  - 配合 `SessionManager` 做更精细的会话隔离与路由。

