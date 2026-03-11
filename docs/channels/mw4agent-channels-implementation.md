# MW4Agent Channels（OpenClaw 风格）实现说明

本文档说明 MW4Agent 中按 OpenClaw channels 架构实现的**可扩展通道层**，以及首个 `console` 通道如何打通端到端链路。

## 目标

- 复刻 OpenClaw 的核心理念：**Dock（轻）+ Plugin（重）+ 统一 InboundContext + 统一 Dispatch**
- 先实现一个最小但完整的 `console` 通道链路：
  - stdin 入站
  - 统一 dispatch（mention gating → agent）
  - stdout 出站
- 后续可以按同样模式增加 Telegram/Discord/Webhook 等通道插件

## 目录结构

```
mw4agent/channels/
├── types.py                # InboundContext/OutboundPayload 等核心类型
├── dock.py                 # ChannelDock（轻量策略入口）
├── mention_gating.py        # mention gating（OpenClaw 风格）
├── registry.py             # ChannelRegistry（注册/查找）
├── dispatcher.py           # ChannelDispatcher（inbound → agent → outbound）
└── plugins/
    ├── base.py             # ChannelPlugin 抽象
    └── console.py          # Console 通道插件（stdin/stdout）
```

## 核心概念

### 1) ChannelDock（轻）

`ChannelDock` 只放共享路径需要的“便宜”策略与能力。第一版只实现了：

- `resolve_require_mention`：群聊是否必须 @ 才触发

对应 OpenClaw：`src/channels/dock.ts`。

### 2) ChannelPlugin（重）

`ChannelPlugin` 是通道适配器的抽象，必须实现：

- `run_monitor(on_inbound)`：监听入站消息并回调
- `deliver(payload)`：发送出站消息

对应 OpenClaw：`ChannelPlugin`（插件注册表）+ 各通道 monitor + 出站发送逻辑。

### 3) InboundContext（统一入站信封）

所有通道入站都要被标准化为 `InboundContext`，至少包含：

- `channel/text`
- `session_key/session_id/agent_id`
- gating 相关字段：`chat_type/was_mentioned/command_authorized/sender_is_owner`

对应 OpenClaw：`MsgContext/FinalizedMsgContext`（这里先做最小集合）。

### 4) ChannelDispatcher（统一 dispatch）

`ChannelDispatcher.dispatch_inbound(ctx)`：

- mention gating（群聊 require-mention）
- 调用 `AgentRunner.run(...)`
- 将 `AgentRunResult.payloads` 转换为 `OutboundPayload`
- 调用 `plugin.deliver(...)`

对应 OpenClaw：`dispatchInboundMessage` → `dispatchReplyFromConfig` → `getReplyFromConfig` → `runReplyAgent`（这里先简化为“直接跑 agent”）。

## Console 通道（端到端）

### CLI 入口

运行 console monitor：

```bash
python3 -m mw4agent channels console run
```

退出：

- 输入 `/quit` 或 `/exit`
- 或 stdin EOF

### 行为

- 每行输入视为一个 inbound 消息
- 统一形成 `InboundContext(channel=\"console\")`
- 走 `ChannelDispatcher` → `AgentRunner` → stdout 输出

## 扩展新通道的方式

1. 新建插件：`mw4agent/channels/plugins/<channel>.py`
2. 实现 `ChannelPlugin`：
   - `run_monitor(...)`：从 SDK/webhook/轮询等拿到消息，转换成 `InboundContext`
   - `deliver(...)`：把 `OutboundPayload` 发回通道
3. 在 CLI 或启动代码中注册：
   - `get_channel_registry().register_plugin(MyChannelPlugin())`

## 下一步（建议）

- 引入更接近 OpenClaw 的 shared pipeline：命令/指令 parsing、队列/去重、threading、allowFrom/ownerAllowFrom
- 把 AgentRunner 的“LLM + tools loop”接上真正的 provider
- 为每个通道加入 dock 策略（groups/mentions/threading/outbound chunking）

