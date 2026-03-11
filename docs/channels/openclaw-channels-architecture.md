# OpenClaw Channels 架构与流程分析

本文档总结 OpenClaw 的 channels（通道层）代码架构与端到端处理流程，作为 MW4Agent 未来实现多通道接入、统一路由与权限/激活控制的参考。

## 核心设计：Dock（轻）+ Plugin（重）

OpenClaw 将通道层拆为两种层级的抽象：

- **`ChannelDock`（轻量）**：给共享代码路径使用的“便宜”元数据与策略入口，避免引入监控/登录/网络探测等重依赖。
  - 典型职责：`allowFrom` 格式化、默认 target、群聊 require-mention、线程 replyTo 策略、command/mention gating 的通道差异化。
  - 代码：`src/channels/dock.ts`
- **`ChannelPlugin`（重逻辑）**：完整的通道适配器（配置/配对/安全/群策略/mentions/outbound/status/gateway/actions/heartbeat/agentTools…）。
  - 代码：`src/channels/plugins/types.plugin.ts`、`src/channels/plugins/index.ts`（运行时 registry）

`dock.ts` 里也明确了设计约束：共享代码应优先依赖 dock/registry，而不是 plugins registry（后者可能很“重”）。

## 注册与元信息

### 核心通道顺序与 meta

- **通道顺序**：`src/channels/registry.ts` 的 `CHAT_CHANNEL_ORDER`
- **通道 meta**：同文件的 `CHAT_CHANNEL_META`（label、docsPath、blurb 等）
- **别名**：`CHAT_CHANNEL_ALIASES`

这些 meta 更多服务于选择/展示、能力声明与统一 ID 规范。

### 插件 registry（运行时）

`src/channels/plugins/index.ts` 从 active plugin registry 读取 channel plugins，并做去重与排序缓存：

- `listChannelPlugins()`：返回排序后的 plugins 列表
- `getChannelPlugin(id)`：按 ID 查 plugin

注意：该模块被刻意标为“heavy”，共享代码路径建议用 `dock.ts`。

## 入站主流程（从平台事件到统一 dispatch）

通道适配器（monitor / event handler）把平台事件转成统一的消息上下文，然后进入 auto-reply/agent 处理管线。

整体链路可以概括为：

1. **monitor 接收平台消息**
2. **构造统一上下文**（`MsgContext` → `FinalizedMsgContext`）
3. **记录 session 元信息（last route、thread binding 等）**
4. **调用 `dispatchInboundMessage(...)`**
5. **进入 `dispatchReplyFromConfig(...)` → `getReplyFromConfig(...)`**
6. **命令/指令/mention gating/队列策略 → 决定是否跑 agent**
7. **生成 reply payloads**
8. **通过通道侧提供的 `deliver(payload)` 发回平台**

### 统一入口：`dispatchInboundMessage`

- 代码：`src/auto-reply/dispatch.ts`
- 关键点：先 `finalizeInboundContext(...)`，再调用 `dispatchReplyFromConfig(...)`

### 示例：Discord 入站

Discord 的 monitor 会构造 `ctxPayload`，把通道特有信息折叠进统一字段：

- `From/To/SessionKey/ChatType`
- `WasMentioned`（mention gating 输入）
- `CommandAuthorized`（命令授权输入）
- `OriginatingChannel/OriginatingTo`（跨 provider 路由用）
- thread 相关字段（`MessageThreadId`、`ParentSessionKey` 等）

代码：`src/discord/monitor/message-handler.process.ts`

### 示例：Signal 入站

Signal 的 event handler 在准备好 dispatcher（含 typing/deliver 回调）后，直接：

- 调用 `dispatchInboundMessage({ ctx: ctxPayload, cfg, dispatcher, replyOptions })`

代码：`src/signal/monitor/event-handler.ts`

## 通道策略在共享管线中的落点

### 1) 群聊 require-mention 与激活模式

OpenClaw 会根据通道 dock 的 group adapter 决定群聊是否必须 @ 才触发。

- 决策函数：`src/auto-reply/reply/groups.ts` 的 `resolveGroupRequireMention(...)`
- 通用 gating 逻辑：`src/channels/mention-gating.ts`

简化理解：

- `requireMention && canDetectMention && !effectiveWasMentioned` → **跳过**本次处理
- 允许某些场景“绕过 mention”（例如群里发控制命令时），由 `resolveMentionGatingWithBypass(...)` 判定

### 2) 命令/指令 gating（commands + directives）

通道会把 `CommandAuthorized`（以及 `OwnerAllowFrom` 等上下文）注入 ctx；auto-reply 层再结合配置与通道能力决定：

- 是否接受 slash commands（`/new`、`/reset`、自定义 skill command 等）
- 是否允许 inline directives（模型切换、队列、status 等）

（具体授权模型可参考已写的权限文档：`docs/agents/openclaw-permission-control.md`）

### 3) 线程/回复路由（threading adapter）

通道 dock/plugin 的 `threading` adapter 会影响：

- reply-to mode（是否总回线程、只首条回线程等）
- 工具上下文里的 thread 标识（例如 Telegram 需要用 topic/thread id，而不是 reply message id）

典型实现可以在 `src/channels/dock.ts` 的 Telegram dock 中看到（通过 `buildToolContext` 规范 threadId/currentMessageId 的来源与格式）。

## 出站（回复发送）模式

OpenClaw 的出站不是“channels 层统一 send()”，而是：

- auto-reply/agent 产出 `ReplyPayload`（可能分块、可能 draft streaming、可能工具直接发送）
- **通道侧**提供 `deliver(payload)` 实现，负责：
  - 平台 API 调用
  - 限长分块与格式化
  - typing 信号
  - 媒体发送

从架构角度看：**dispatch 管线与平台发送解耦**，通道只需要实现“把 payload 发出去”。

## 关键文件索引（OpenClaw）

- **Dock（轻）**
  - `src/channels/dock.ts`
  - `src/channels/registry.ts`
- **Plugin（重）**
  - `src/channels/plugins/types.plugin.ts`
  - `src/channels/plugins/index.ts`
- **入站统一 dispatch**
  - `src/auto-reply/dispatch.ts`
  - `src/auto-reply/reply/dispatch-from-config.ts`
  - `src/auto-reply/reply/get-reply.ts`
- **mention gating**
  - `src/channels/mention-gating.ts`
  - `src/auto-reply/reply/groups.ts`
- **通道示例**
  - `src/discord/monitor/message-handler.process.ts`
  - `src/signal/monitor/event-handler.ts`

## 对 MW4Agent 的落地启示（简版）

如果 MW4Agent 要复刻 OpenClaw 的 channels 架构，建议：

- **定义轻量 Dock 层**：只放共享管线必需的策略/能力/格式化函数，确保依赖稳定、加载快。
- **定义 Plugin/Adapter 层**：每个平台的“监控入站 + 发送出站 + 配置/配对/安全/线程/群策略”等放这里。
- **统一 Inbound Context**：所有通道先映射到统一结构，再进入一条 dispatch 管线（命令/指令/mention gating/队列/agent）。
- **出站通过回调注入**：dispatch 管线只产出 payload，由通道注入的 deliver 负责发送，避免共享代码依赖通道 SDK。

