# OpenClaw：Session 短期记忆（Session Memory）实现分析

本文总结 OpenClaw 在 **session 级短期记忆**（可理解为“对话历史 / 上下文”）上的实现：它如何存储、如何在下一轮对话时加载/裁剪/修复，并最终如何注入到 LLM 请求中。

> 说明：OpenClaw 的“长期记忆”通常指 memory 插件（例如 `memory_search` / `memory_get`）。本文聚焦 **session 内短期记忆**（历史对话），而不是长期记忆插件。

## 目标与边界

- **目标**：让每个 `sessionKey`（DM/群聊/网页会话等）拥有连续可追溯的对话上下文，并在后续回合自动带上历史消息，形成“短期记忆”。
- **边界**：
  - 短期记忆主要由**会话转录（transcript）**承载。
  - 会话元数据（model/provider/覆盖项/统计等）存入 **session store**，用于定位 transcript 文件并提供运行参数。
  - 会话历史会被 **sanitize / 修复 / 截断**，以降低 token 消耗并避免模型协议约束导致的错误。

## 存储：session store + transcript（JSONL）

OpenClaw 将 session 的“短期记忆”拆为两层：

### 1）Session store：`sessions.json`

- **作用**：保存 session 元数据（`sessionId`、`updatedAt`、`sessionFile`、模型覆盖、token 统计、compaction 计数等），并以 `sessionKey` 作为索引。
- **位置**：默认在 OpenClaw state dir 下按 agent 分隔（`agents/<agentId>/sessions/sessions.json`）。
- **相关实现**：
  - `src/config/sessions/paths.ts`
    - `resolveDefaultSessionStorePath(agentId?)` → `.../agents/<agentId>/sessions/sessions.json`
  - `src/config/sessions/store.ts`
    - `loadSessionStore()` 负责读取/缓存/迁移
  - `src/config/sessions/types.ts`
    - `SessionEntry` 定义 session 元数据结构（含 `sessionFile`、`compactionCount`、token 字段等）

### 2）Transcript：`<sessionId>.jsonl`

- **作用**：保存对话历史（短期记忆的主体）。
- **格式**：JSONL（一行一个 JSON 对象），第一行通常是 session header，后续是 message/compaction/custom entries。
- **位置**：默认在 `agents/<agentId>/sessions/` 下（与 store 同目录），文件名基于 `sessionId`：
  - `src/config/sessions/paths.ts`
    - `resolveSessionTranscriptPath(sessionId, agentId?, topicId?)` → `.../agents/<agentId>/sessions/<sessionId>.jsonl`
- **写入**：OpenClaw 通过 `@mariozechner/pi-coding-agent` 的 `SessionManager` 统一追加消息，避免破坏“叶子节点 parentId 链”。
  - `src/config/sessions/transcript.ts`
    - `SessionManager.open(sessionFile).appendMessage(...)`
  - `src/agents/pi-embedded-runner/run/attempt.ts`
    - 同样通过 `SessionManager.open(...)+append...` 维护 transcript

## 读取与注入：对话时如何把短期记忆带入 LLM

OpenClaw 的“把 session 短期记忆注入 LLM”可以概括为：

1. **定位 transcript 文件**
2. **加载 transcript → 得到历史 messages**
3. **sanitize/修复/截断历史**
4. **将历史 messages + system prompt + 本轮 prompt 一起交给 LLM**

下面按关键路径拆解。

### 1）定位会话文件（sessionFile / sessionId）

OpenClaw 用 `sessionKey` 在 session store 中定位 `SessionEntry`，并通过 `resolveSessionFilePath(...)` / `resolveAndPersistSessionFile(...)` 选择/生成实际的 transcript 路径（`sessionFile`）。

- 入口示例（CLI / agent 命令）：`src/commands/agent.ts`
  - 解析 `sessionKey`、`sessionEntry`，并最终得到 `sessionFile`
- Gateway 侧也会用 session store + sessionKey 定位 sessionFile（例如 history/注入等 RPC）
  - `src/gateway/server-methods/chat.ts`

### 2）加载 transcript 形成历史 messages

在 Embedded Pi Agent 路径（主流）中，OpenClaw 创建 agent session 时把 `sessionManager` 传入 `createAgentSession(...)`，由 Pi 的 session 层负责把 transcript 还原成 `activeSession.messages`：

- `src/agents/pi-embedded-runner/run/attempt.ts`
  - `sessionManager = SessionManager.open(params.sessionFile)`
  - `({ session } = await createAgentSession({ ..., sessionManager, ... }))`
  - 此时 `activeSession.messages` 就是“短期记忆（历史对话）”的内存表示

### 3）sanitize / 修复 / 截断历史（短期记忆治理）

历史消息加载出来并不会直接喂给模型，OpenClaw 在一次 run 里会做多层治理：

#### 3.1 修复 SessionManager 持久化边界（确保首条 user 不丢）

Pi 的 `SessionManager` 有一个“文件已存在但尚无 assistant 消息时 flushed=true 导致首条 user 不落盘”的行为，OpenClaw 通过预处理修正：

- `src/agents/pi-embedded-runner/session-manager-init.ts`
  - `prepareSessionManagerForRun(...)`：必要时清空文件，只保留 header，确保首轮写入顺序正确

#### 3.2 清洗历史：模型兼容性与安全裁剪

OpenClaw 会在 attempt 开始后对 `activeSession.messages` 做 sanitize/校验：

- `src/agents/pi-embedded-runner/run/attempt.ts`
  - `sanitizeSessionHistory({ messages: activeSession.messages, ... })`
  - `validateGeminiTurns(...)` / `validateAnthropicTurns(...)`（按 provider 策略）
  - `dropThinkingBlocks(...)`（某些 provider/CLI 可能拒绝历史 thinking 块）
  - `sanitizeToolCallIdsForCloudCodeAssist(...)` 等工具调用兼容处理

#### 3.3 截断历史：按“用户回合数”限制短期记忆长度

OpenClaw 支持按 sessionKey（区分 DM/群聊）配置历史回合上限，并把历史裁剪到最后 N 个 user turn：

- `src/agents/pi-embedded-runner/history.ts`
  - `limitHistoryTurns(messages, limit)`
  - `getHistoryLimitFromSessionKey(sessionKey, config)`
- `src/agents/pi-embedded-runner/run/attempt.ts`
  - `const truncated = limitHistoryTurns(validated, getDmHistoryLimitFromSessionKey(...))`

裁剪之后，为避免把 tool_use 的 assistant 消息裁掉导致 tool_result “孤儿化”，OpenClaw 可能再次修复 tool_use/tool_result 配对：

- `src/agents/pi-embedded-runner/run/attempt.ts`
  - `sanitizeToolUseResultPairing(...)`

#### 3.4 防止连续 user turn：修复 orphaned trailing user message

如果 transcript 末尾出现“最后一条是 user，但没有对应 assistant”（可能来自中断/崩溃），下一轮 prompt 会形成连续 user turn，部分模型会报错。OpenClaw 通过 `SessionManager.getLeafEntry()` 检测并回退 leaf：

- `src/agents/pi-embedded-runner/run/attempt.ts`
  - `const leafEntry = sessionManager.getLeafEntry()`
  - `sessionManager.branch(leafEntry.parentId)` / `sessionManager.resetLeaf()`
  - `activeSession.agent.replaceMessages(sessionManager.buildSessionContext().messages)`

### 4）注入到 LLM：system prompt + history + 本轮 prompt

完成历史治理后，OpenClaw 通过 `activeSession.prompt(effectivePrompt, ...)` 发起本轮调用。此时：

- **system prompt**：由 OpenClaw 构建，并通过 `applySystemPromptOverrideToSession(activeSession, systemPromptText)` 写入 session
  - `src/agents/pi-embedded-runner/system-prompt.ts`
- **history messages（短期记忆）**：在 `activeSession.messages` 中（来自 transcript → sanitize → truncate → repair）
- **本轮用户输入**：`effectivePrompt`（可能被插件 `before_prompt_build` 注入前置上下文）
  - `src/agents/pi-embedded-runner/run/attempt.ts`
    - `resolvePromptBuildHookResult(...)` 可 prepend context

最终效果：**LLM 每一轮看到的是 “system prompt + 被治理后的历史消息 + 当前 prompt”**，从而实现 session 级短期记忆。

## Gateway 的 history 读取（用于 UI/调试，不等同于 LLM 注入）

Gateway 侧的 `chat.history` 主要用于把 transcript 展示给 UI。它读取 JSONL 并抽取 `parsed.message`：

- `src/gateway/session-utils.fs.ts`
  - `readSessionMessages(sessionId, storePath, sessionFile?)`：
    - 逐行解析 JSON
    - `parsed.message` → UI messages
    - `type === "compaction"` → 生成一个轻量 “system divider” 便于 UI 展示

这条链路强调“可视化/检索”，而 LLM 注入链路强调“创建 AgentSession 时加载历史 + 尝试前治理”。

## 小结：OpenClaw session 短期记忆的关键点

- **双层存储**：
  - `sessions.json`：索引与元数据（定位 transcript、记录统计/覆盖项）
  - `<sessionId>.jsonl`：短期记忆主体（历史对话）
- **加载后必须治理**：
  - provider 协议差异（thinking/tool_call_id 等）
  - token 成本控制（按 user turn 截断）
  - transcript 结构修复（leaf 回退、tool_use/tool_result 配对修复）
- **注入方式**：
  - 不是手工拼接 messages，而是通过 `createAgentSession(..., sessionManager)` 将 transcript 映射为 `activeSession.messages`，再 `activeSession.prompt(...)` 完成本轮调用。

---

## MW4Agent vs OpenClaw：现状差异与待补能力（短期记忆）

本节用于对齐 OpenClaw 的 session 短期记忆链路，并记录 MW4Agent 当前实现的差异点与后续补齐清单（持续更新）。

### 1）存储结构差异

- **OpenClaw**
  - 双层存储：`sessions.json`（Session store）+ `<sessionId>.jsonl`（Transcript）。
  - Transcript 不仅有 message，还可能包含 compaction/custom entries，并由 Pi `SessionManager` 维护 leaf/parentId 链以支持 branch/reset。
- **MW4Agent（当前）**
  - Session 元数据：`mw4agent/agents/session/manager.py`（加密 JSON，字段较少）。
  - Transcript：`mw4agent/agents/session/transcript.py`（JSONL：header + 多行 `type=message`，每行保存一个 OpenAI 风格 message）。
  - 目前没有 leaf/parentId 链、branch/resetLeaf，也没有 compaction/custom entry。

### 2）读取与注入链路差异（是否“每轮自动带历史”）

- **OpenClaw**：将 transcript 还原为 `activeSession.messages`，在一次 run 内执行 sanitize/repair/truncate，再由 `activeSession.prompt(...)` 统一注入（system + history + 本轮 prompt）。
- **MW4Agent（当前）**：
  - 已具备 transcript 的读写与最小裁剪函数（`read_messages` / `limit_history_user_turns` / `drop_trailing_orphan_user`）。
  - 但尚未完全做到 OpenClaw 那种“每轮 LLM 调用前的标准化流程：load → 治理 → 注入”的统一入口（更多是能力已具备，但链路仍需进一步收敛与加固）。

### 3）历史治理能力差异（OpenClaw 更完整）

OpenClaw 在一次 run 中会对历史做多层治理，MW4Agent 当前主要缺少：

- **provider 协议校验与修复**：如 Gemini/Anthropic turn 校验、thinking 块处理、tool_call_id 兼容等。
- **tool_use / tool_result 配对修复**：历史裁剪后避免出现“孤儿 tool result / 缺失 tool result”。
- **leaf 回退 / branch 机制**：遇到崩溃/中断导致末尾 orphan user message，OpenClaw 通过 leafEntry.parentId 回退；MW4Agent 当前多为简单 drop。
- **compaction（历史压缩）**：OpenClaw 可将旧历史压缩为摘要继续增长；MW4Agent 当前暂无。
- **按 sessionKey 差异化 history limit**：OpenClaw 可按 DM/群聊不同限额；MW4Agent 当前以全局 limit 为主。

### 4）与 OpenClaw “sessions 纳入统一 memory search”相比的缺口

OpenClaw 会把 `"sessions"` 作为 memory sources 之一写入统一索引（embedding + FTS），因此 `memory_search` 可检索到近期会话内容。MW4Agent 当前：

- `mw4agent.memory.search` 主要检索 workspace 的 md（MEMORY.md、memory/*.md 等）。
- **尚未把 session transcript 作为 source 纳入 memory_search**（短期记忆与长期记忆检索尚未打通）。
- 也尚未具备 OpenClaw 的增量同步策略（`deltaBytes/deltaMessages/onSessionStart/onSearch/watch/interval`），因为当前 Phase 1 仍是文件关键词检索。

### 5）待补清单（建议优先级）

- **P0（短期记忆是否稳定生效）**
  - 在 Runner 中形成统一链路：**加载 transcript → sanitize/repair → truncate → 注入到每轮 LLM messages**。
  - 增加基础的 tool history 配对修复，避免生成非法序列。
- **P1（体验/稳定性）**
  - provider-specific history validate（按 provider 的 turns 协议）。
  - 按 sessionKey/聊天类型配置 historyLimitTurns。
  - compaction：将旧历史摘要化写入 transcript（降低 token 成本）。
- **P2（与 OpenClaw memory 系统对齐）**
  - 将 sessions transcript 纳入 memory_search sources（先 keyword，后续 embedding+FTS）。
  - 增量同步与 watcher/interval 等策略。

---

## MW4Agent 当前已落地的 OpenClaw/Pi 风格能力（阶段性记录）

本节记录已在 MW4Agent 中实现的“Pi/OpenClaw transcript 关键机制”，用于后续持续对齐。

### 1）Transcript 扩展：entry 类型与链结构

文件：`mw4agent/agents/session/transcript.py`

- **message entry 支持 id/parentId 链**：
  - `append_messages(...)` 现在会为每条 message 生成 `id`（entryId）并写入 `parentId`，形成链式结构。
- **leaf 指针记录**：
  - 通过追加 `type=leaf` 记录维护当前 leaf（不删除历史，可用于 branch/reset）。
- **compaction/custom entries（基础版）**：
  - `append_compaction(...)`：写入 `type=compaction`，并将摘要作为一条 `role=system` 的 message，同时把 leaf 指向 compaction entry。
  - `append_custom(...)`：写入 `type=custom`，用于记录非 message 的自定义数据（不注入为对话消息）。
- **leaf 链重建**：
  - `build_messages_from_leaf(...)`：按 leaf/parentId 只重建当前分支可达的 messages。
- **branch/reset（基础版）**：
  - `branch_to_parent(transcript_file, parent_id)`：通过写入 leaf 指针实现“回退/切换分支”。
  - `get_leaf_entry_meta(...)`：读取当前 leaf 对应的 (leaf_id, parent_id, leaf_message)，用于实现 orphan user 回退等治理。

### 2）Runner 注入：按 leaf 分支加载历史 + orphan user 回退

文件：`mw4agent/agents/runner/runner.py`

- 在每轮调用前，短期记忆历史的构建逻辑已升级为：
  - 优先使用 `build_messages_from_leaf(transcript_file)`（尊重 branch/reset）；
  - 若 leaf 指向的最后一条是 `role=user`（常见于中断/崩溃），则自动 `branch_to_parent(parent_id)` 回退 leaf，再重建历史；
  - 最后用 `drop_trailing_orphan_user(...)` 做兜底，避免出现连续 user turn。

### 3）测试覆盖

文件：`tests/test_session_transcript_memory.py`

- 覆盖：
  - message roundtrip + history limit（原有）；
  - leaf/parentId 链 + branch 回退；
  - compaction 写入为 system message，并可通过 branch 回退到 compaction 之前的分支。

