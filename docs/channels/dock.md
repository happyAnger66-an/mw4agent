# ChannelDock：含义与使用场景（对比 OpenClaw 与 MW4Agent）

## 1. ChannelDock 的核心含义

**`ChannelDock` 是“通道的轻量元数据 + 可被共享路径读取的策略/适配器入口”。**

- **轻量（cheap）**：应当“便宜到可以被任意共享代码 import”，不应引入通道 SDK、网络调用、长连接、重依赖等副作用。
- **共享可读**：用于让共享流程（mentions/commands/threading/streaming/allowlist 等）在不依赖具体 channel plugin 的情况下，读取通道差异化策略与能力信息。
- **重逻辑下沉到 Plugin**：真正的通道实现（monitor/deliver/登录/配对/探测/重试/限流等）属于 `ChannelPlugin`。

## 2. MW4Agent 中的 ChannelDock（当前实现）

MW4Agent 的 `ChannelDock` 很精简，重点是：

- **能力描述**：`capabilities`
- **策略钩子（第一版）**：`resolve_require_mention` → `require_mention()`

典型使用链路：

1. **Plugin 构造时定义 dock**（例如 console：永远不要求 mention）
2. **Registry 注册 plugin 时缓存 dock**
3. **Dispatcher 在 dispatch_inbound 阶段读 dock 做 mention gating**

这使得 `dispatch_inbound` 可以在“触发 agent 之前”快速做出是否跳过的决策，而不需要触碰通道 SDK/重依赖。

## 3. OpenClaw 中的 ChannelDock（更完整的 dock 设计）

OpenClaw 的 `ChannelDock` 更接近“通道的只读描述 + 共享路径适配器集合”，覆盖更多共享场景：

- **commands**：命令语义/适配、部分命令权限默认策略等
- **outbound**：出站分片/字符限制（例如不同平台的消息长度上限）
- **streaming**：流式输出合并策略的默认参数
- **config**：allowFrom/defaultTo 等配置的解析/格式化
- **groups**：群聊策略（require-mention、group tool policy 等）
- **mentions**：mention 识别/清理（strip patterns / stripMentions）
- **threading**：线程/回复上下文构建
- **agentPrompt**：与 agent prompt 相关的通道适配（如需要）

OpenClaw 还明确要求：**共享代码应优先依赖 dock/registry，而不是直接依赖 plugin registry**，以避免把通道重依赖引入到核心共享路径。

## 4. Dock 的典型使用场景（放 Dock vs 放 Plugin）

### 4.1 适合放 Dock 的东西（轻量、纯策略/纯函数/配置解析）

- **群聊是否 require mention**（MW4Agent 已用；OpenClaw groups 也有）
- **mention 清理/strip 规则**（OpenClaw mentions adapter）
- **出站消息分片/字符限制**（OpenClaw outbound.textChunkLimit）
- **streaming 合并默认参数**（OpenClaw streaming defaults）
- **threading 上下文构建的轻量规则**（thread id / reply-to 的抽象映射）
- **allowFrom/defaultTo 的解析与格式化**（从配置中解析并归一化）

原则：这类逻辑应当 **可测试、无副作用、无 SDK 依赖、导入成本低**。

### 4.2 适合放 Plugin 的东西（重逻辑：SDK/网络/状态/副作用）

- 监听消息（webhook/长轮询/ws/SDK event）
- 发送消息（SDK API 调用、重试、限流、媒体上传）
- 登录/配对、状态探测、会话维护
- 任何需要通道 SDK 或会触发网络/IO 副作用的逻辑

原则：Plugin 是“重适配器”，Dock 是“轻描述/轻策略”。

## 5. Dock 与 Gateway 的关系（常见误解澄清）

Dock 本身并不决定“是否必须经过 Gateway”。

- **Dock 解决的问题**：共享流程如何读取各通道差异化策略/能力，而不引入 plugin 的重依赖。
- **Gateway 解决的问题**：agent 的统一入口、事件流、幂等、wait 语义、监控与状态快照等。

在 MW4Agent 中，Dispatcher 目前支持两种执行路径：

- **Gateway 模式**：通过 Gateway RPC 调 agent（对齐 OpenClaw 的运行入口模型）
- **直连模式**：直接调用 `AgentRunner`（开发/测试的简化模式）

但无论哪种路径，Dock 都可以在“进入执行前”提供 gating / capabilities 等共享决策。

