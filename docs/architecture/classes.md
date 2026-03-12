# MW4Agent 核心类关系图（概览）

本文以简化类关系图的形式，概览 MW4Agent 当前已实现的核心组件及其关联关系（省略非关键属性和部分辅助类）。

---

## 1. Agent 执行核心

```text
+------------------------+
| AgentRunParams         |
+------------------------+
          |
          v
+------------------------+        uses          +------------------------+
| AgentRunner            |--------------------->| generate_reply(...)    |
|  - session_manager     |                      | (llm.backends)         |
|  - event_stream        |                      +------------------------+
|  - tool_registry       |
+------------------------+
          |
          v
+------------------------+
| SessionManager         |
|  - sessions: dict      |
|  - session_file (enc)  |
+------------------------+
          ^
          |
+------------------------+
| SessionEntry (dataclass|
+------------------------+

+------------------------+
| ToolRegistry           |
|  - tools: name->tool   |
+------------------------+
          ^
          |
   registers
          |
+------------------------+      subclass       +-------------------------+
| AgentTool (base)       |<-------------------| GatewayLsTool           |
|  + execute(...)        |                    +-------------------------+
+------------------------+
```

- `AgentRunner`：一次智能体 turn 的执行入口，负责：
  - 会话接入（`SessionManager`）
  - 事件流（`event_stream`）
  - 工具调用（`ToolRegistry` + `AgentTool`）
  - LLM 调用（`llm.backends.generate_reply`）
- `SessionManager`：负责按 `session_key` / `session_id` 管理会话，并将会话数据加密落盘。
- `ToolRegistry` / `AgentTool`：工具体系注册表与抽象基类，`GatewayLsTool` 是一个具体工具示例。

---

## 2. Channels 层

```text
+------------------------+
| ChannelDispatcher      |
|  - runtime: ChannelRuntime
|  - registry: ChannelRegistry
+------------------------+
          |
   dispatch_inbound(ctx)
          v
+------------------------+       has          +------------------------+
| ChannelRegistry        |------------------->| ChannelPlugin (base)   |
|  - _plugins[id]        |        ^           |  - id/meta/caps/dock    |
|  - _docks[id]          |        |           |  + run_monitor(...)     |
+------------------------+   subclasses       |  + deliver(...)         |
          ^                                 +-----------+--------------+
          |                                 |ConsoleChannel / Feishu...|
   global singleton                         +---------------------------+
          |
+------------------------+
| ChannelDock            |
|  - id                  |
|  - capabilities        |
|  - resolve_require_mention()
+------------------------+

+------------------------+
| InboundContext         |
| OutboundPayload        |
+------------------------+
```

- `ChannelDispatcher`：统一的“入站 → Agent → 出站”调度器：
  - 从各通道 plugin 接收 `InboundContext`
  - 结合 `ChannelDock` + mention gating 决定是否触发 Agent
  - 调用 `AgentRunner` 执行
  - 将 `OutboundPayload` 交给具体通道的 `deliver()` 发送。
- `ChannelRegistry`：维护各通道的 `ChannelPlugin` 与 `ChannelDock`。
- `ChannelPlugin`：通道适配器基类，具体实现如 `ConsoleChannel`、`FeishuChannel` 等。
- `ChannelDock`：轻量策略与元数据（能力、是否 require mention 等），供共享路径读取。

---

## 3. Gateway 与运行状态

```text
+------------------------+
| FastAPI app (Gateway)  |
|  /rpc: agent/agent.wait|
|  /ws: events           |
+------------------------+
          |
          v
+------------------------+
| GatewayState           |
|  - runs: run_id->RunRecord
|  - dedupe: key->DedupeEntry
+------------------------+
          |
          +--> RunRecord
          |     - snapshot: RunSnapshot
          |
          +--> DedupeEntry

+------------------------+
| RunSnapshot            |
|  - run_id,status       |
|  - started_at,ended_at |
|  - error,reply_text    |
+------------------------+
```

- Gateway FastAPI 应用提供：
  - `/rpc`：`agent` / `agent.wait` 等 JSON-RPC 风格接口；
  - `/ws`：推送 Agent 生命周期 / assistant / tool 事件的 WebSocket 流。
- `GatewayState`：
  - `runs`：跟踪每个 `run_id` 的 `RunRecord`；
  - `dedupe`：根据幂等 key（`idempotencyKey`）缓存已处理请求的终态。
- `RunSnapshot`：记录一次 run 的最终状态（成功 / 失败 / 超时）及时间戳，必要时包含 `reply_text`。

---

## 4. 配置 / Skills / 加密存储

```text
+------------------------+
| EncryptedFileStore     |
|  - key (AES-GCM)       |
|  + read_json/write_json|
+------------------------+
          ^
          |
+------------------------+      +------------------------+
| ConfigManager          |      | SkillManager          |
|  - config_dir          |      |  - skills_dir         |
|  (calls EncryptedFS)   |      |  (calls EncryptedFS)  |
+------------------------+      +------------------------+
```

- `EncryptedFileStore`：对称加密读写（AES-GCM），用于所有敏感文件（sessions/config/skills 等）。
- `ConfigManager`：
  - 负责 `~/.mw4agent/config/*.json`（或 `MW4AGENT_CONFIG_DIR`）的加密读写；
  - 用于管理 `llm.json` 等配置。
- `SkillManager`：
  - 负责 `~/.mw4agent/skills/*.json` 的加密读写；
  - 支持列出 / 批量读取技能定义。

---

## 5. LLM 与 Mock Server

```text
+------------------------+
| generate_reply(...)    |
|  - _load_llm_config()  |
|  - echo / openai       |
+------------------------+
          |
          v (if openai)
+------------------------+
| _call_openai_chat(...) |
|  - base_url =          |
|    MW4AGENT_OPENAI_BASE_URL
+------------------------+
          |
   HTTP POST /v1/chat/completions
          v
+------------------------+
| Mock LLM Server        |
|  (FastAPI, OpenAI兼容) |
+------------------------+
```

- `generate_reply`：
  - 从 `AgentRunParams` / 环境变量 / `llm.json` 解析 provider 与 model；
  - 默认使用 `echo` 后端；
  - 当 provider=`openai` 且有 `OPENAI_API_KEY` 时，通过 `_call_openai_chat` 调用兼容 OpenAI 的接口。
- `_call_openai_chat`：
  - 默认 base URL 为 `https://api.openai.com`；
  - 通过 `MW4AGENT_OPENAI_BASE_URL` 可重定向到本地 mock server，方便测试。
- Mock LLM Server（`mw4agent.llm.mock_server`）：
  - FastAPI 应用，提供 `POST /v1/chat/completions`；
  - 永远返回 200，回显最后一条 user 消息，结构兼容 OpenAI Chat Completions。

---

以上关系图概括了当前 MW4Agent 的主要类与模块耦合情况，可作为继续扩展（新通道、新 LLM provider、新工具）时的参考框架。 

