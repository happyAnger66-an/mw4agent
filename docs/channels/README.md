# Channels 文档

本目录用于沉淀对 **OpenClaw Channels**（通道层）的架构/流程分析，以及后续在 **MW4Agent** 中复刻通道层设计时的参考材料。

## Feishu Channel 使用方式（当前）

### 0. 随 Gateway 一起启动（推荐，对齐 OpenClaw）

若已配置 Feishu（见下节），**直接启动 Gateway 即可**：Feishu Webhook 会挂载在同一进程内，无需再单独执行 `channels feishu run`。

```bash
# 先配置 feishu（app_id / app_secret），任选其一：
mw4agent channels feishu add --app-id <APP_ID> --app-secret <APP_SECRET>
# 或：mw4agent configuration set-channels --channel feishu --app-id <APP_ID> --app-secret <APP_SECRET>

# 启动 Gateway（Feishu 事件订阅 URL 填同一地址的 /feishu/webhook）
mw4agent gateway run --bind 0.0.0.0 --port 18790
```

- 默认使用 **webhook**：飞书应用后台「事件订阅」请求 URL 填 `http://<网关地址>:18790/feishu/webhook`（或你的公网/ngrok 地址）。
- 若在配置中设置 **`connection_mode: "websocket"`**，则随 Gateway 启动的是 lark-oapi 长连接，无需在飞书后台填请求 URL。

### 0.1 多个飞书应用（多账号 + 绑定不同 Agent）

在 `channels.feishu.accounts` 下为每个应用写一组凭证，并可指定 `agent_id`。**启动 Gateway 时会自动注册全部账号**（各自 webhook 路径或各自 WS 连接）。

```json
{
  "channels": {
    "feishu": {
      "connection_mode": "webhook",
      "accounts": {
        "sales": {
          "app_id": "cli_sales",
          "app_secret": "...",
          "agent_id": "sales_bot"
        },
        "support": {
          "app_id": "cli_support",
          "app_secret": "...",
          "agent_id": "support_bot",
          "webhook_path": "/feishu/webhook/support"
        }
      }
    }
  }
}
```

- CLI：`mw4agent channels feishu add --account sales --app-id ... --app-secret ... --agent-id sales_bot`
- 运行时入站 `InboundContext.channel` 为 `feishu:sales` 等；工具策略未单独配置时可回退到 `tools.by_channel.feishu`。

### 1. 配置凭证与连接模式

Feishu 需要 **App ID** 和 **App Secret**，任选其一即可。连接模式 **connection_mode** 决定随 Gateway 启动时用 webhook 还是 websocket（默认 `webhook`）：

- **方式 A：配置文件**（推荐）
  ```bash
  # 专用子命令（与 set-channels 等价，写入 channels.feishu）
  mw4agent channels feishu add --app-id <APP_ID> --app-secret <APP_SECRET>
  # 省略参数时会交互式提示；也可用环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET

  # webhook（默认）：飞书事件订阅填 Gateway 的 /feishu/webhook
  mw4agent configuration set-channels --channel feishu --app-id <APP_ID> --app-secret <APP_SECRET>

  # 使用 websocket（lark-oapi 长连接，无需在飞书填请求 URL）
  mw4agent channels feishu add --app-id <APP_ID> --app-secret <APP_SECRET> --connection-mode websocket
  ```
  写入 `~/.mw4agent/mw4agent.json` 的 `channels.feishu`（含 `connection_mode`），入站/出站都会自动读取。

- **方式 B：环境变量**
  ```bash
  export FEISHU_APP_ID="<APP_ID>"
  export FEISHU_APP_SECRET="<APP_SECRET>"
  ```
  环境变量优先于配置文件。WebSocket 模式还可选：`FEISHU_ENCRYPT_KEY`、`FEISHU_VERIFICATION_TOKEN`。

### 2. 单独启动 Feishu Channel（可选）

需要单独进程时（例如使用 WebSocket 模式或不同端口），可执行：

```bash
mw4agent channels feishu run [选项]
```

常用选项：

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `8081` | 监听端口 |
| `--path` | `/feishu/webhook` | Webhook 路径（飞书事件订阅填此 URL） |
| `--mode` | `webhook` | `webhook`（HTTP 回调）或 `websocket`（lark-oapi 长连接） |
| `--gateway-url` | （空） | 若设置，则通过 Gateway RPC 调 Agent（如 `http://127.0.0.1:18790`） |
| `--session-file` | `mw4agent.sessions.json` | 会话文件路径 |

示例：

```bash
# 仅本机、Webhook 模式
mw4agent channels feishu run --host 127.0.0.1 --port 8081

# 使用 WebSocket 模式（需安装 lark-oapi）
mw4agent channels feishu run --mode websocket

# 对接已有 Gateway
mw4agent channels feishu run --gateway-url http://127.0.0.1:18790
```

### 3. 飞书开放平台侧

- **Webhook 模式**：在飞书应用后台配置「事件订阅」请求 URL：`https://<你的公网域名或 ngrok>/feishu/webhook`，并订阅需要的消息事件（如「接收消息」）。
- **WebSocket 模式**：无需配置请求 URL，由 SDK 主动建连；凭证同上。

### 4. 数据流简述

1. **入站**：飞书事件（Webhook POST 或 WebSocket 推送）→ `FeishuChannel.run_monitor` 解析为 `InboundContext` → `ChannelDispatcher.dispatch_inbound` → 直接调 `AgentRunner` 或通过 Gateway RPC。
2. **出站**：Agent 返回 → `OutboundPayload` → `FeishuChannel.deliver` → `feishu_outbound.send_text` → `FeishuClient` 调飞书 Open API 发文本到对应 `chat_id`（从 `payload.extra["inbound"]["extra"]` 取）。

当前仅支持文本消息；群聊默认需 @ 机器人才会触发。

---

## 文档列表

- [OpenClaw Channels 架构与流程](../architecture/channels/openclaw-channels-architecture.md)
- [MW4Agent Channels 实现说明](../architecture/channels/mw4agent-channels-implementation.md)
- [Dispatcher 设计说明](../architecture/channels/dispatcher-design.md)：Channels 如何调用 Agent（Gateway RPC vs 直接调用）

