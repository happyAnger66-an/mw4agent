## OpenClaw 多 Agents 实现分析（并映射到 mw4agent）

本文基于 OpenClaw 源码梳理其 **multi-agents**（多 agent 配置与隔离）和 **sessions 管理** 的核心实现方式，并给出 mw4agent 侧要对齐的落地建议：在 `~/.mw4agent/agents/<agentId>/...` 下创建独立 agent 目录、独立 session store、独立 transcripts（JSONL）。

---

## 1. OpenClaw 的核心对象：agentId、sessionKey、agentDir

### 1.1 agentId 与 sessionKey 前缀

OpenClaw 将“当前运行属于哪个 agent”编码进 sessionKey，约定形如：

- `agent:<agentId>:<rest>`

解析逻辑在 `openclaw/src/routing/session-key.ts` 的 `parseAgentSessionKey()`：

- 只要 sessionKey 以 `agent:` 开头并至少包含 3 段，就能解析出 `agentId` 与 `rest`
- 返回值会统一做 **lowercase 归一化**，便于稳定比较与路由

此外还提供了：

- `isSubagentSessionKey()`：识别 `subagent:` 前缀（以及被 `agent:<id>:` 包裹的 subagent）
- `resolveThreadParentSessionKey()`：对 thread/topic 形式的 sessionKey 做“父 key”提取

### 1.2 选择“本次 run 的 agentId”

OpenClaw 在 `openclaw/src/agents/agent-scope.ts` 中提供了统一的 agent 选择逻辑：

- `resolveDefaultAgentId(cfg)`：从 `cfg.agents.list` 中找 `default=true` 的 agent（多个会 warn，取第一个）
- `resolveSessionAgentIds({ sessionKey, config, agentId })`：
  - 优先显式传入的 `agentId`
  - 否则从 sessionKey 的 `agent:<id>:` 前缀解析
  - 再否则回退到 default agent

这保证了：

- 同一进程可以同时服务多个 agent
- agent 维度的配置、workspace、会话存储都能按 sessionKey 自动路由

---

## 2. OpenClaw 的 session 存储：按 agent 分隔的 store + transcripts

### 2.1 state 目录（~/.openclaw）

OpenClaw 的可变状态目录由 `openclaw/src/config/paths.ts` 的 `resolveStateDir()` 决定：

- 默认：`~/.openclaw`
- 可通过 `OPENCLAW_STATE_DIR` 覆盖

### 2.2 sessions 目录结构（关键）

OpenClaw 将每个 agent 的 sessions 存在：

- `~/.openclaw/agents/<agentId>/sessions/`

实现来自 `openclaw/src/config/sessions/paths.ts`：

- `resolveSessionTranscriptsDirForAgent(agentId)` → `.../agents/<agentId>/sessions`
- `resolveDefaultSessionStorePath(agentId)` → `.../agents/<agentId>/sessions/sessions.json`

其中：

- `sessions.json`：session store（key → SessionEntry），记录 sessionId、更新时间、投递上下文等元信息
- `*.jsonl`：该 agent 下每个 session 的 transcript 文件（通常通过 sessionId 关联）

并且 `resolvePathWithinSessionsDir()` 内置了多种兼容逻辑：

- 支持旧版本把 transcript 路径存成 absolute path 的情况（会尝试归一化）
- 支持从 absolute path 中结构化提取 agentId（`.../agents/<agentId>/sessions/<file>`）并做回退定位

### 2.3 何时写入 transcripts

OpenClaw 在 `openclaw/src/config/sessions/transcript.ts` 中提供了 transcript 追加能力（示例：`appendAssistantMessageToSessionTranscript`）：

- 通过 `resolveDefaultSessionStorePath(agentId)` 定位该 agent 的 store
- 从 store 读取 sessionKey 对应的 SessionEntry（拿到 sessionId）
- 再根据 sessionId 解析/落盘到 `.../agents/<agentId>/sessions/<sessionId>.jsonl`（或类似命名）

---

## 3. 多 session / 子 agent（subagent）与可见性控制

### 3.1 sessions_spawn：创建“隔离会话”

OpenClaw 的 `sessions_spawn` 工具实现位于 `openclaw/src/agents/tools/sessions-spawn-tool.ts`：

- 支持 runtime：`subagent`（以及 `acp`，本文重点是 subagent）
- 支持两种模式：
  - `mode="run"`：一次性任务型
  - `mode="session"`：持久会话型（通常与 thread 绑定，用于后续跟进）
- `thread=true` 时会尝试绑定到消息线程（需要 channel 插件提供 hooks）
- 产物：返回 childSessionKey / runId 等信息，并由子 agent 完成后自动回传结果（强调“不要轮询”）

### 3.2 subagent 运行记录与自动回传

subagent 的运行登记/恢复/announce 由 `openclaw/src/agents/subagent-registry.ts` 维护：

- 维护 runId → SubagentRunRecord 的内存态
- 定期持久化、恢复（避免进程重启丢状态）
- 结束后触发 announce 流程，将结果回传给 requester session
- 还会对“孤儿 run”（找不到 session entry / sessionId）做清理

### 3.3 session 工具可见性（树/跨 agent）

OpenClaw 对 sessions_list/history/send 等 session 工具提供了额外的可见性边界（不是 tools.profile 那一套）：

实现：`openclaw/src/agents/tools/sessions-access.ts`

- `tools.sessions.visibility`：
  - `self`：只允许当前 session
  - `tree`（默认）：当前 session + 其 spawn 出来的子 session（最常用）
  - `agent`：同一 agent 的任意 session
  - `all`：跨 agent
- 跨 agent 还需要额外满足 `tools.agentToAgent.enabled=true` 且 allow 规则允许（同文件 `createAgentToAgentPolicy`）
- sandbox 场景下还会额外 clamp（默认只允许 spawned）

这套机制本质是：

- **session 工具不是“全局无限可见”**，而是按 session tree / agent / a2a policy 受控

---

## 4. mw4agent 要对齐的目录与会话管理建议

你提出的目标是：支持在 `~/.mw4agent/agents/` 下创建不同 agent 目录并进行 session 管理。对齐 OpenClaw 的话，建议直接采用同款结构：

### 4.1 建议目录结构

```
~/.mw4agent/
  agents/
    <agentId>/
      sessions/
        sessions.json
        <sessionId>.jsonl
      workspace/        (可选：该 agent 的默认 workspace)
      memory/           (可选：该 agent 的持久记忆)
```

### 4.2 关键路由原则（建议）

- **agentId 必须可从 sessionKey 解析**（建议采用 `agent:<agentId>:...` 作为 canonical 前缀）
- **session store 与 transcripts 必须按 agentId 分隔**：
  - store：`~/.mw4agent/agents/<agentId>/sessions/sessions.json`
  - transcripts：同目录下 `*.jsonl`
- **sessions 工具的可见性应默认“tree”**（当前 session + spawned），并支持提升到 agent/all（需要额外 gate）

### 4.3 建议实现切入点

在 mw4agent 的实现中可以分三步落地：

1) **引入 agent-scope**：统一 `defaultAgentId` / `resolveSessionAgentId` / `agent sessionKey` 规范  
2) **引入 sessions store per agent**：将当前 `mw4agent.sessions.json` 演进为 `~/.mw4agent/agents/<agentId>/sessions/sessions.json`  
3) **引入 transcripts per agent**：将每个 session 的对话落盘为 jsonl，并为 memory/search 复用（类似 OpenClaw 的 `memory/session-files.ts`）

---

## 5. 结论

OpenClaw 的 multi-agents 不是“多个进程”，而是通过：

- sessionKey 里编码 agentId（`agent:<id>:`）
- state 目录下按 agentId 分隔 sessions store 与 transcripts（`~/.openclaw/agents/<id>/sessions/...`）
- sessions 工具再叠加“可见性边界”（tree/agent/all + agent-to-agent gate）

从而实现 **多 agent 配置、工作区、会话与历史的隔离与可控共享**。mw4agent 若要对齐，最关键的是先把 **agentId → sessions 目录路由** 建起来，并把 session store/transcripts 迁移到 `~/.mw4agent/agents/<agentId>/sessions/`。

