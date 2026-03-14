# OpenClaw LLM Thinking 模式实现与作用

本文基于 openclaw 仓库源码，说明 **Thinking 模式**（扩展思考/推理）的实现方式及其作用。

---

## 1. 概念区分

OpenClaw 里有两类与“思考”相关的概念，容易混淆：

| 概念 | 含义 | 主要作用 |
|------|------|----------|
| **ThinkLevel / thinkingLevel** | 控制 **模型端** 的“扩展思考”强度（如 Claude extended thinking、OpenRouter reasoning.effort）。 | 决定 API 请求里是否开启、以及多强的“思考预算”；不直接决定前端是否展示思考内容。 |
| **ReasoningLevel** | 控制 **前端/会话** 是否展示模型返回的 **推理块**（thinking/reasoning 内容）。 | `off` 隐藏推理；`on`/`stream` 显示推理内容（如 TUI 的 `[thinking]`、UI 的 reasoning 块）。 |

二者配合：ThinkLevel 决定“模型是否多想想”；ReasoningLevel 决定“用户是否看到这些想法”。

---

## 2. ThinkLevel 的定义与层级

### 2.1 类型与归一化

- **定义位置**：`src/auto-reply/thinking.ts`
- **类型**：`ThinkLevel = "off" | "minimal" | "low" | "medium" | "high" | "xhigh" | "adaptive"`
- **归一化**：`normalizeThinkLevel(raw)` 把用户输入（如 `"on"`、`"think-hard"`、`"max"`）映射到上述枚举；`"on"`→`low`，`"adaptive"`/`"auto"`→`adaptive`，`"xhigh"`/`"extrahigh"`→`xhigh` 等。
- **特殊**：
  - **xhigh**：仅部分模型支持（如 GPT-5.4 等），由 `supportsXHighThinking(provider, model)` 与 `XHIGH_MODEL_REFS` 判定；会话 patch 时若当前 provider/model 不支持 xhigh 会退回 `high`。
  - **adaptive**：在 pi-agent-core 侧映射为 `medium`，由 SDK/提供商再转为 `thinking.type: "adaptive"` 等（如 Opus 4.6 / Sonnet 4.6 的 `output_config.effort: "medium"`）。
  - **Binary 提供商**（如 Z.AI）：`isBinaryThinkingProvider(provider)` 为 true 时，UI 只展示 `off` / `on` 两档。

### 2.2 会话与 Gateway 的存储

- **会话字段**：`SessionEntry.thinkingLevel?: string`（如 `src/gateway/session-utils.types.ts`、`protocol/schema/sessions.ts`）。
- **Patch**：`sessions.patch` 支持 `thinkingLevel: string | null`；校验时用 `normalizeThinkLevel`，非法值会报错并提示可用档位（`formatThinkingLevels(provider, model, "|")`）；xhigh 仅在支持的模型上允许，否则自动改为 high。
- **下发到 agent**：Gateway 在调用 agent 时从 session entry 读出 `entry.thinkingLevel`，传给 run 参数（如 `server-methods/chat.ts` 的 `resolveThinkingDefault`、`server-methods/agent.ts` 的 `thinkingLevel: entry?.thinkingLevel`），最终进入 **run 的 attempt 参数** `thinkLevel`。

---

## 3. 在 Agent 运行中的使用

### 3.1 从 Run 参数到 pi-agent-core

- **Attempt 参数**：`EmbeddedRunAttemptParams.thinkLevel: ThinkLevel`（`run/types.ts`）；若上游未传则会有默认（如 `"off"`）。
- **映射**：`mapThinkingLevel(thinkLevel)`（`pi-embedded-runner/utils.ts`）将 OpenClaw 的 `ThinkLevel` 转为 **pi-agent-core** 的 `ThinkingLevel`：
  - `undefined`/未传 → `"off"`
  - `"adaptive"` → `"medium"`（由 SDK/提供商再解释为 adaptive）
  - 其余 `off`、`minimal`、`low`、`medium`、`high`、`xhigh` 原样传递。
- **注入 Session**：在 `run/attempt.ts` 里创建 agent session 时，把 `thinkingLevel: mapThinkingLevel(params.thinkLevel)` 传给 pi-agent-core 的 session 配置（约 1185 行），从而影响该次对话的 **API 请求参数**（是否带 thinking、effort 等）。

### 3.2 系统提示中的展示

- **Runtime 行**：`buildAgentSystemPrompt` 会拼一行 Runtime 信息，其中包含 `thinking=${defaultThinkLevel ?? "off"}`（`system-prompt.ts` 的 `buildRuntimeLine`），让模型“知道”当前会话的思考档位（仅提示用，实际生效仍靠 API 参数）。

---

## 4. 按提供商的 API 注入方式

实际“是否开启思考、强度多少”由各提供商的 **stream 包装器** 在请求 payload 里注入，OpenClaw 在 `applyExtraParamsToAgent`（`extra-params.ts`）里根据 provider 挂载不同 wrapper。

### 4.1 Anthropic（Claude extended thinking）

- pi-agent-core / pi-ai 已支持 Claude 的 extended thinking；OpenClaw 传入的 `thinkingLevel` 会进入 session，由 SDK 转为对应 API 参数（如 `thinking` 相关字段）。
- 若有 Google 专用逻辑，会走 `createGoogleThinkingPayloadWrapper`（见下）。

### 4.2 Google（Gemini thinkingConfig）

- **包装器**：`createGoogleThinkingPayloadWrapper(baseStreamFn, thinkingLevel)`（`extra-params.ts`）。
- **作用**：在 `onPayload` 里对 `model.api === "google-generative-ai"` 的 payload 做两件事：
  - **清理**：若 `thinkingConfig.thinkingBudget < 0`（如 -1），删除 `thinkingBudget`，避免无效/负值导致后端错误。
  - **注入**：对 Gemini 3.1 系列且 `thinkingLevel !== "off"` 时，用 `mapThinkLevelToGoogleThinkingLevel(thinkingLevel)` 得到 `MINIMAL`|`LOW`|`MEDIUM`|`HIGH`，写入 `config.thinkingConfig.thinkingLevel`（若尚未存在）。
- **映射**：minimal→MINIMAL，low→LOW，medium/adaptive→MEDIUM，high/xhigh→HIGH；off 不写。

### 4.3 OpenRouter / Kilocode（reasoning.effort）

- **包装器**：`createOpenRouterWrapper`、`createKilocodeWrapper`（`proxy-stream-wrappers.ts`）。
- **逻辑**：`normalizeProxyReasoningPayload(payload, thinkingLevel)`：
  - 若 `thinkingLevel` 为 off 或未传，不注入 reasoning。
  - 否则在 payload 的 `reasoning` 对象中注入或补全 `effort`：`effort = mapThinkingLevelToOpenRouterReasoningEffort(thinkingLevel)`，取值为 `"none"`|`"minimal"`|`"low"`|`"medium"`|`"high"`|`"xhigh"`（off→none，adaptive→medium）。
- **例外**：`modelId === "auto"`（OpenRouter）或 `kilo/auto`（Kilocode）以及 `isProxyReasoningUnsupported(modelId)`（如部分 x-ai 模型）时**不注入** reasoning，避免不支持的模型报错。

### 4.4 其他（Moonshot、SiliconFlow、Z.AI 等）

- **Moonshot / SiliconFlow**：有专门的 `createMoonshotThinkingWrapper`、`createSiliconFlowThinkingWrapper` 等，用于在对应 API 上打开或兼容 thinking 参数。
- **Z.AI**：通过 `createZaiToolStreamWrapper` 等控制 `tool_stream` 等；thinking 档位若由 pi-agent-core 支持则随 session 传入。

---

## 5. ReasoningLevel：推理内容的展示

- **类型**：`ReasoningLevel = "off" | "on" | "stream"`（`auto-reply/thinking.ts`）；归一化函数 `normalizeReasoningLevel`。
- **作用**：不改变 API 请求，只影响 **前端/会话** 是否展示模型返回的 reasoning/thinking 块：
  - **off**：不展示（隐藏）。
  - **on**：展示。
  - **stream**：以流式/草稿形式展示。
- **系统提示**：在 `buildAgentSystemPrompt` 里有一行说明：`Reasoning: ${reasoningLevel} (hidden unless on/stream). Toggle /reasoning; /status shows Reasoning when enabled.`，提示模型“推理内容默认对用户隐藏，除非开启 on/stream”。
- **前端**：Control UI 的 chat 根据 `reasoningLevel !== "off"` 和 `showThinking` 决定是否渲染 reasoning 块；TUI 用 `extractThinkingFromMessage` 抽 thinking 块，按 `showThinking` 决定是否输出 `[thinking]` 等。

---

## 6. 端到端数据流小结

1. **会话**：用户或 UI 通过 `sessions.patch` 设置 `thinkingLevel`（及可选 `reasoningLevel`），Gateway 持久化到 SessionEntry。
2. **请求**：chat/agent 等入口从 entry 读出 `thinkingLevel`（缺省时用 `resolveThinkingDefault`），传给 `runEmbeddedPiAgent` → attempt 的 `thinkLevel`。
3. **Run**：`mapThinkingLevel(thinkLevel)` 转为 pi-agent-core 的 `ThinkingLevel`，写入 session 配置；system prompt 里 Runtime 行显示 `thinking=...`。
4. **API**：根据 provider 用不同 wrapper 在 payload 中注入：
   - Google：`thinkingConfig.thinkingLevel`（及清理 thinkingBudget）；
   - OpenRouter/Kilocode：`reasoning.effort`；
   - 其他由 pi-agent-core/SDK 或专用 wrapper 处理。
5. **展示**：模型若返回 thinking/reasoning 块，前端根据 **ReasoningLevel**（及 showThinking）决定是否展示；ThinkLevel 不直接控制展示，只控制“有没有、多强”的思考。

---

## 7. 相关文件索引（openclaw 仓库）

- **ThinkLevel 定义与归一化**：`src/auto-reply/thinking.ts`（ThinkLevel、ReasoningLevel、normalizeThinkLevel、listThinkingLevels、supportsXHighThinking 等）。
- **会话与 Gateway**：`src/gateway/session-utils.types.ts`、`sessions-patch.ts`（thinkingLevel patch、xhigh 校验）、`server-methods/chat.ts`（resolveThinkingDefault）、`server-methods/agent.ts`（下发给 agent）。
- **Agent Run**：`src/agents/pi-embedded-runner/run/types.ts`（thinkLevel）、`run/attempt.ts`（传入 session）、`utils.ts`（mapThinkingLevel）、`system-prompt.ts`（defaultThinkLevel 进 prompt）、`extra-params.ts`（Google/OpenRouter/Kilocode 等 wrapper）、`proxy-stream-wrappers.ts`（reasoning.effort）。
- **UI**：`ui/src/ui/views/sessions.ts`（thinking/reasoning 下拉）、`views/chat.ts`（showReasoning）、`controllers/sessions.ts`（patch）、`controllers/chat.ts`（thinkingLevel 请求）。
- **TUI / 格式化**：`src/tui/tui-formatters.ts`（extractThinkingFromMessage、thinking 块）、`tui-stream-assembler.ts`、`tui-command-handlers.ts`（/thinking 命令）。

---

## 8. 与 MW4Agent 的对照

- **AgentRunParams**（`mw4agent/agents/types.py`）已支持：
  - **`thinking_level`**：对应 OpenClaw 的 ThinkLevel，在调用 LLM 时根据 provider 写入对应 API 参数（若接 OpenRouter/Google 则映射 effort 或 thinkingConfig；OpenAI 暂无标准 extended thinking 可预留）。
  - **`reasoning_level`**：对应 ReasoningLevel（`off` | `on` | `stream`），仅影响是否向客户端下发/展示推理块，不改变 API 请求参数。

### 8.1 在 Gateway 中打开 Thinking / Reasoning

MW4Agent Gateway 的 **`agent`** RPC 已支持通过 params 传入：

- **`thinkingLevel`**：与 ThinkLevel 一致，可选 `off` | `minimal` | `low` | `medium` | `high` | `xhigh` | `adaptive`。
- **`reasoningLevel`**：与 ReasoningLevel 一致，可选 `off` | `on` | `stream`。

示例请求：

```json
{
  "jsonrpc": "2.0",
  "method": "agent",
  "params": {
    "message": "用户消息",
    "idempotencyKey": "唯一键",
    "thinkingLevel": "medium",
    "reasoningLevel": "on"
  },
  "id": "1"
}
```

Gateway 将上述参数映射为 `AgentRunParams.thinking_level` 与 `AgentRunParams.reasoning_level` 传给 Runner；Runner 与 LLM 据此决定是否开启扩展思考及是否下发/展示推理内容。详见 [MW4Agent Gateway 与 Agent 交互](../../architecture/gateway/mw4agent-gateway-agent-interaction.md)。
