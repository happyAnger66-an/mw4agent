# OpenClaw 工具与技能执行原理：如何根据用户消息决定执行哪些工具 / Skills

本文基于 openclaw 仓库源码，说明 **OpenClaw 如何根据用户消息决定要执行哪些工具（tools）和技能（skills）**，以及实际执行动作的链路。

---

## 1. 结论概览

- **谁在做“判断”**：不是 OpenClaw 用规则或路由根据用户消息“选工具”，而是 **大模型（LLM）** 在每轮回复时根据当前对话内容、系统提示和工具列表，**自行决定** 是输出纯文本还是发起 **tool_calls**（调用哪些工具、传什么参数）。
- **OpenClaw 的职责**：准备好 **工具集合**（含 schema）、**系统提示**（含 skills 说明与工具摘要）、**会话历史**，交给 pi-agent-core 与上游 LLM API；执行则由 pi-agent-core 的 **Agent 循环** 完成：发请求 → 收到 text 或 tool_calls → 若为 tool_calls 则执行对应工具、把结果写回 transcript → 再请求 LLM，直到模型不再返回 tool_calls。

因此：**“根据用户消息判断要执行哪些工具 / skills”** 的本质是 **LLM 基于自然语言理解 + 系统提示里的工具/技能描述做出的决策**；OpenClaw 只做“提供工具列表 + 写清何时用技能/工具”的准备工作。

---

## 2. 工具（Tools）的准备与下发

### 2.1 工具列表从哪里来

- **入口**：一次 embedded agent run 在 `pi-embedded-runner/run/attempt.ts` 里构建本次 run 的“工具列表”。
- **创建函数**：`createOpenClawCodingTools()`（`src/agents/pi-tools.ts`），根据当前会话/通道/沙箱/配置等参数，组装一整套 OpenClaw 工具，例如：
  - 读写与编辑：`read`、`write`、`apply_patch`，以及沙箱版 `sandboxed_read` / `sandboxed_write` 等；
  - 会话与消息：`sessions_list`、`sessions_send`、`message` 等（来自 channel-tools、pi-tools）；
  - 节点与网关：`nodes`（nodes-tool）、`gateway`（gateway-tool）、`cron`（cron-tool）等；
  - 其他：`memory_search`、`memory_get`、`web_search`、`web_fetch`、`browser`、`agent_step`、`subagents` 等。
- **策略与过滤**：在放入 session 之前会经过：
  - **tool-policy**：owner-only、allowlist、subagent 策略等，会过滤掉当前 run 不允许的工具；
  - **tool-fs-policy**：是否限制在 workspace 内；
  - **channel-tools / message-provider**：按通道能力或消息来源（如 voice）禁用部分工具（例如 TTS）；
  - **model-provider**：例如 xAI 时禁用 OpenClaw 的 `web_search` 避免与模型自带能力重复。
- **最终形态**：工具以 **OpenAI/Anthropic 等兼容的 function/tool 定义（name + description + parameters schema）** 传给 pi-agent-core，再由其转成各 provider 的 API 格式（如 `tools` / `functions`）。  
- **代码位置**：  
  - 工具集合构建：`src/agents/pi-tools.ts`（`createOpenClawCodingTools`）、`src/agents/pi-embedded-runner/run/attempt.ts`（约 854–884 行：`toolsRaw = createOpenClawCodingTools(...)`）；  
  - 策略与过滤：`src/agents/tool-policy*.ts`、`src/agents/pi-tools.policy.ts`、`src/agents/channel-tools.ts` 等。

### 2.2 工具如何被“选”中并执行

- **选择**：完全由 **LLM 在推理时** 根据当前 turn 的 **用户消息 + 历史消息 + 系统提示（含工具名与摘要）** 决定是否在本轮输出 `tool_calls`，以及调用哪个工具、传什么参数。没有单独的“用户消息 → 工具”路由代码。
- **执行**：pi-agent-core 的 run loop 在收到模型返回的 `tool_calls` 后，按 `name` 在已注册的工具列表中查找实现，传入参数并执行；结果写回 transcript，再继续请求 LLM。  
- 因此：**“根据用户消息判断要执行哪些工具”** = **模型看到用户消息 + 系统里的工具描述后，在生成的那一步决定调用哪些工具**；OpenClaw 只负责把“当前允许的工具集合 + 描述”交给模型。

---

## 3. 技能（Skills）如何参与“执行”

### 3.1 Skills 不是独立 API 工具

- OpenClaw 的 **skills** 不会以“一个 skill = 一个 API tool”的形式注册到 LLM。  
- 它们以 **一段系统提示（skills prompt）** 的形式注入：告诉模型“当前有哪些可用技能（名称、简短描述、SKILL.md 路径）”，以及**使用规则**。

### 3.2 Skills 提示的生成与注入

- **解析与快照**：  
  - 若本次 run 已有 **skillsSnapshot**（例如 Gateway/控制面预先算好的），则直接使用 `skillsSnapshot.prompt` 作为 skills 相关系统内容。  
  - 否则通过 `resolveEmbeddedRunSkillEntries()`（`pi-embedded-runner/skills-runtime.ts`）决定是否从 workspace 加载 skill entries，再通过 `resolveSkillsPromptForRun()`（`src/agents/skills/workspace.ts`）生成 prompt。
- **resolveSkillsPromptForRun**：  
  - 若存在 `skillsSnapshot.prompt`，直接返回；  
  - 否则用 `buildWorkspaceSkillsPrompt(workspaceDir, { entries, config })` 从 workspace / managed / bundled 等目录加载技能列表，用 `formatSkillsForPrompt(skills)` 生成 `<available_skills>` 等结构化文本（含 name、description、location 等）。
- **注入系统提示**：  
  - `buildEmbeddedSystemPrompt()`（`pi-embedded-runner/system-prompt.ts`）会把上述 **skillsPrompt** 交给 `buildAgentSystemPrompt()`（`src/agents/system-prompt.ts`）。  
  - 在 system-prompt 里会有一段 **Skills (mandatory)** 的固定说明，例如：先扫描 `<available_skills>` 的 description；若恰好有一个技能明显适用则用 **read** 工具读该技能的 SKILL.md（location），再按技能说明执行；若多个可能适用则选最具体的一个再读；若都不适用则不读任何 SKILL.md。

因此：**“根据用户消息判断要用哪些 skills”** 同样是 **模型根据用户消息 + 系统提示里的技能列表与规则** 自行决定；若模型认为需要某个技能，它会先调用 **read** 工具去读对应 SKILL.md，再按技能内容执行（可能再调用其它工具）。Skills 的“执行”是通过 **read + 后续工具调用** 在 Agent 循环里完成的，而不是由 OpenClaw 单独解析用户意图再选 skill。

### 3.3 相关代码位置

- Skills 快照 / 条目解析与 prompt 生成：`src/agents/skills/workspace.ts`（`buildWorkspaceSkillsPrompt`、`resolveSkillsPromptForRun`）、`src/agents/pi-embedded-runner/skills-runtime.ts`（`resolveEmbeddedRunSkillEntries`）。  
- 系统提示中 Skills 段落：`src/agents/system-prompt.ts`（`buildSkillsSection`、`buildAgentSystemPrompt` 中对 `skillsPrompt`、`toolNames`、`toolSummaries` 的使用）。  
- Run 时传入 skillsSnapshot / skillEntries：`src/agents/pi-embedded-runner/run/attempt.ts`（约 776–795 行：skill entries、`resolveSkillsPromptForRun`、`buildEmbeddedSystemPrompt` 的 `skillsPrompt`）。

---

## 4. 端到端：从用户消息到实际执行

1. **用户发出一条消息** → 进入 Gateway/agent 入口，最终调用 `runEmbeddedPiAgent`（如 `pi-embedded-runner/run.ts`）。
2. **构建本次 run 的上下文**：  
   - 解析/加载 session、workspace、config；  
   - 解析 skillsSnapshot 或加载 skill entries，得到 **skillsPrompt**；  
   - 调用 `createOpenClawCodingTools(...)` 得到 **工具列表**，再经策略过滤；  
   - 构建 **系统提示**（含 skills 段落 + 工具名列表 + 工具摘要 + 其它固定段落）。
3. **交给 pi-agent-core**：  
   - 创建或恢复 session，设置 system prompt；  
   - 把 **工具定义（schemas）** 和 **当前对话历史（含最新用户消息）** 发给 LLM API。
4. **LLM 返回**：  
   - 若返回 **纯文本**：直接作为助手回复，可结束本 turn；  
   - 若返回 **tool_calls**：pi-agent-core 按 name 找到对应工具实现，执行并把结果 append 到 transcript，再带完整历史重新请求 LLM。
5. **循环**直到模型不再返回 tool_calls（或达到其它终止条件）。  
6. 其中“是否调用工具、调用哪个、参数是什么”以及“是否先 read 某个 SKILL.md 再执行”**全部由模型在每轮生成时决定**；OpenClaw 只保证：  
   - 提供的工具集合和策略一致；  
   - 系统提示里写清了技能列表和使用方式；  
   - 工具实现（如 nodes、gateway、read、write 等）在收到调用时正确执行并返回结果。

---

## 5. 小结表

| 问题 | 答案 |
|------|------|
| 谁根据用户消息决定“要执行哪些工具”？ | **LLM**。OpenClaw 只提供工具列表 + 描述 + 策略过滤，不根据用户消息做规则路由。 |
| 谁根据用户消息决定“要用哪些 skills”？ | **LLM**。Skills 以系统提示中的“技能列表 + 使用规则”出现；模型若认为需要某技能，会先用 **read** 读 SKILL.md，再按技能内容可能调用其它工具。 |
| 工具列表从哪里来？ | `createOpenClawCodingTools()`（pi-tools.ts），再经 tool-policy、channel-tools、model-provider 等过滤后传给 pi-agent-core。 |
| Skills 如何参与？ | 通过 **skillsPrompt** 注入系统提示，描述“有哪些技能、何时用 read 读 SKILL.md”；实际“执行”技能 = 模型调用 read + 后续工具。 |
| 实际执行动作的代码在哪？ | 各工具实现分散在 `src/agents/tools/*.ts`（如 nodes-tool、gateway-tool、cron-tool、sessions-*、memory-*、web-* 等）；执行循环与 transcript 更新在 **pi-agent-core**（外部依赖）中。 |

---

## 6. 相关文档与仓库路径

- 仓库：openclaw（与 mw4agent 同级或独立 clone）。  
- 关键文件：  
  - `src/agents/pi-embedded-runner/run/attempt.ts`（工具与 system prompt 的组装、skills 注入）；  
  - `src/agents/pi-tools.ts`（`createOpenClawCodingTools`）；  
  - `src/agents/system-prompt.ts`（`buildAgentSystemPrompt`、Skills 段落）；  
  - `src/agents/skills/workspace.ts`（`resolveSkillsPromptForRun`、`buildWorkspaceSkillsPrompt`）；  
  - `src/agents/pi-embedded-runner/system-prompt.ts`（`buildEmbeddedSystemPrompt`）；  
  - `src/agents/tools/*.ts`（各工具实现）。  
- MW4Agent 侧可参考：`docs/openclaw/nodes.md`、`docs/architecture/gateway/agent_call_gateway.md`。
