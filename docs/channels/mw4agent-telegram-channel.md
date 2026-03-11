# MW4Agent Telegram 通道架构与实现

本文档说明 MW4Agent 中 Telegram 通道的架构设计与实现方式，基于 OpenClaw 的 Channels 模型（Dock + Plugin + Dispatcher），并结合 Telegram Bot API 做了 Python 化落地。

## 1. 目标与能力边界

- **目标**
  - 将 Telegram Bot 的消息作为一个标准通道接入 MW4Agent；
  - 复用现有的 `ChannelDispatcher` → `AgentRunner` → 工具/LLM 流程；
  - 尽量保持实现“轻量、可扩展”，避免直接把业务逻辑写死在通道里。

- **当前能力边界**
  - 使用 **long polling**（`getUpdates`）方式接收消息；
  - 使用 `sendMessage` 将 Agent 回复发回到原始 chat；
  - 未做复杂的命令解析、mention 解析（首版视为「已提及」）；
  - 授权/权限控制仅做了最小占位（`sender_is_owner=False`）。

## 2. 文件与依赖

- 代码文件：
  - `mw4agent/channels/plugins/telegram.py`
- 依赖：
  - 在 `setup.py` 中增加：
    - `httpx>=0.26.0`（用于调用 Telegram Bot API）

## 3. 架构概览

整体沿用 Channels 层的四段式结构：

```text
Telegram Bot API
  ↓ getUpdates / sendMessage
TelegramChannel (ChannelPlugin)
  ↓
ChannelDispatcher.dispatch_inbound()
  ↓
AgentRunner.run()  →  LLM / tools / events
  ↓
OutboundPayload  →  TelegramChannel.deliver() → sendMessage
```

### 3.1 ChannelPlugin：`TelegramChannel`

文件：`mw4agent/channels/plugins/telegram.py`

核心实现：

- 继承自 `ChannelPlugin`，定义：
  - `id = "telegram"`
  - `meta = ChannelMeta(id="telegram", label="Telegram", docs_path="/channels/telegram")`
  - `capabilities = ChannelCapabilities(chat_types=("direct", "group", "channel", "thread"), native_commands=True, block_streaming=False)`
  - `dock = ChannelDock(id="telegram", capabilities=..., resolve_require_mention=lambda _acct: True)`
    - 群聊默认 **require mention = True**，为后续 mention 解析预留策略位。
- 额外字段：
  - `bot_token: str`（必需，来自构造参数或 `TELEGRAM_BOT_TOKEN` 环境变量）
  - `api_base: str = "https://api.telegram.org"`
  - `long_poll_timeout: int = 25`

### 3.2 与 Dispatcher 的协作

- 入站：
  - `TelegramChannel.run_monitor(on_inbound)` 负责：
    1. 使用 `httpx.AsyncClient` 调用 `getUpdates` 进行 long polling；
    2. 将每条 `update` 中的 `message` 映射为 `InboundContext`：
       - `channel="telegram"`
       - `text=message.text`
       - `session_key=f"telegram:{chat.id}"`
       - `session_id=str(chat.id)`
       - `chat_type` 映射自 Telegram 的 `chat.type`：
         - `private` → `direct`
         - `group/supergroup` → `group`
         - `channel` → `channel`
       - 身份信息：
         - `sender_id=from.id`
         - `sender_name=username/first_name`
       - 额外信息：
         - `extra={"chat_id": chat.id, "raw_update": update}`
    3. 调用 `await on_inbound(ctx)` 将上下文交给 `ChannelDispatcher`。

- 出站：
  - `ChannelDispatcher.dispatch_inbound` 在调用 `AgentRunner.run()` 后，会将**入站上下文透传**到 `OutboundPayload.extra["inbound"]` 中：
    - `{"channel": ..., "session_key": ..., "session_id": ..., "extra": ctx.extra, ...}`
  - `TelegramChannel.deliver(payload)` 从中取出 `chat_id`：
    - `payload.extra["inbound"]["extra"]["chat_id"]`
  - 然后通过 `sendMessage` 调用 Telegram Bot API 把 `payload.text` 发回原 chat。
  - 若无法解析 `chat_id`，则退化为打印到 stdout（`[telegram:AI] ...`），避免静默失败。

## 4. CLI 接入方式

文件：`mw4agent/cli/channels/register.py`

- 命令层级：

```bash
mw4agent channels telegram run \
  --session-file mw4agent.sessions.json \
  --bot-token "<TELEGRAM_BOT_TOKEN>"
```

- 入口实现（简化描述）：
  - 从 CLI/环境变量中拿到 `bot_token`；
  - `get_channel_registry().register_plugin(TelegramChannel(bot_token=bot_token))`；
  - 构造：
    - `SessionManager(session_file)`
    - `AgentRunner(session_manager)`
    - `ChannelDispatcher(ChannelRuntime(session_manager, agent_runner))`
  - 调用 `dispatcher.run_channel("telegram")` 启动 long polling 监听循环。

## 5. 行为示例

1. 用户在 Telegram 中向 bot 发送消息："Hello"
2. `TelegramChannel.run_monitor` 通过 `getUpdates` 收到该消息：
   - 构造 `InboundContext(channel="telegram", text="Hello", session_key="telegram:<chatId>", ...)`
   - 交给 `ChannelDispatcher.dispatch_inbound`
3. Dispatcher 做 mention gating（群聊下需要 @ 的策略后续可扩展），然后调用：
   - `AgentRunner.run(AgentRunParams(message="Hello", session_key=..., session_id=...))`
4. Agent 完成一轮 LLM 调用后返回 `AgentRunResult`：
   - Dispatcher 把结果封装成 `OutboundPayload`，并附带 `inbound` 信息；
5. `TelegramChannel.deliver` 解析出 `chat_id`，调用 `sendMessage` 把回复发回到原对话。

## 6. 后续扩展建议

- **mention 解析**：
  - 解析 `entities` 中的 `mention` 和 `bot_command`，精确判断是否「被 @」以及命令触发。
  - 将 `was_mentioned` 和 `command_authorized` 的逻辑下沉到 dock / plugin。

- **权限与 allowFrom**：
  - 基于 chat 类型和 `owner` ID 实现 OpenClaw 风格的 `allowFrom` / `ownerAllowFrom` 权限控制。

- **错误与重试**：
  - 对 `sendMessage` 失败做更细粒度的重试与日志记录。
  - 对 `getUpdates` 的错误增加退避策略与告警钩子。

