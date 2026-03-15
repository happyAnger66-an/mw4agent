# Memory 架构：会话文件短期记忆与 memory_tool / memory-cli

本文总结 OpenClaw 的**会话文件短期记忆**实现架构，以及 MW4Agent 中与之对齐的 **memory_tool**（Agent 工具）与 **memory-cli** 设计。完整 Memory 系统（向量索引、混合检索、MMR、时间衰减等）见 [docs/openclaw/memory.md](../../openclaw/memory.md)。

---

## 1. OpenClaw 会话文件短期记忆架构小结

OpenClaw 的**短期记忆**与长期记忆**共用同一套索引与检索接口**，区别只在数据来源和同步策略：

- **来源**：`sources` 中的 `"sessions"` 对应会话转录文件（如 `state/agents/{agentId}/sessions/*.jsonl`），与 `"memory"`（MEMORY.md、memory/*.md 等）一起被切片、向量化后写入**同一** SQLite（`MemoryIndexManager`）。
- **同步**：`sync.sessions.deltaBytes` / `deltaMessages` 控制“会话累计变化多少再写索引”；`onSessionStart` / `onSearch` 可在会话开始或搜索前触发一次同步，保证近期对话可被搜到。
- **检索**：仍走统一的 `search()`，在混合打分 + **时间衰减**下，与当前 session 相关的最近消息会自然排在前面，无需单独“短期记忆 API”。

因此“会话文件短期记忆”并不是独立系统，而是：**会话日志 → 增量写入同一向量/FTS 索引 → 与长期记忆一起被 search**。

---

## 2. MW4Agent Phase 1：memory_tool 与 memory-cli

MW4Agent 已实现与 OpenClaw 对齐的 **memory_tool**（Agent 工具）和 **memory-cli**（CLI），当前为**仅文件、无向量**的 Phase 1。

### 2.1 组件一览

| 组件 | 说明 |
|------|------|
| **mw4agent.memory** | 模块：`list_memory_files(workspace_dir)`、`search(...)`、`read_file(...)`。来源为工作区根下：`AGENTS.md`、`SOUL.md`、`TOOLS.md`、`IDENTITY.md`、`USER.md`、`HEARTBEAT.md`、`BOOTSTRAP.md`、`MEMORY.md`、`memory.md` 以及 `memory/*.md`。检索为关键词匹配（无 embedding）。 |
| **memory_search** | Agent 工具：参数 `query`（必填）、`maxResults`、`minScore`；返回 `results`（path、startLine、endLine、snippet、score）。无可用时返回 `disabled: true`。 |
| **memory_get** | Agent 工具：参数 `path`（必填）、`from`、`lines`；返回 `path`、`text`、`missing`。仅允许读取 `list_memory_files` 中的路径。 |
| **memory status** | CLI：列出工作区、provider（file）、记忆文件列表。 |
| **memory search** | CLI：`memory search <query>` 或 `--query`，支持 `--max-results`、`--min-score`、`--json`。 |
| **memory get** | CLI：`memory get <path>`，支持 `--from`、`--lines`、`--json`。 |

### 2.2 Memory 检索结果是否输入 LLM：已实现

**是的，已实现。** 流程如下：

1. **工具注册**：`memory_search`、`memory_get` 与 read/write 等一起在 `agents/tools/__init__.py` 中注册到全局 `ToolRegistry`。
2. **Runner 工具循环**：`AgentRunner._execute_agent_turn` 中取 `tool_definitions = self.tool_registry.get_tool_definitions()`（含 memory_search、memory_get 的 name/description/parameters），若存在工具则走 `_run_tool_loop`。
3. **发给 LLM**：`generate_reply_with_tools(params, messages, tool_definitions)` 将上述 tool_definitions 转成 OpenAI  function 格式，随 `messages` 一起发给 LLM，因此模型可以决定是否调用 `memory_search` / `memory_get`。
4. **执行并回传**：若 LLM 返回 `tool_calls`，Runner 对每条调用 `execute_tool(...)`（会执行到 `MemorySearchTool.execute` / `MemoryGetTool.execute`），将返回的 `result` 序列化为字符串，以 **role=`"tool"`** 的消息追加到 `messages`。
5. **下一轮**：同一循环内再次调用 `generate_reply_with_tools(params, messages, tool_definitions)`，此时 `messages` 中已包含上一步的 tool 结果，LLM 即可基于 memory 检索结果生成最终回复。

因此：**memory 检索结果会作为 tool 消息进入对话历史，并在此后的 LLM 调用中被使用**。无需额外“把 memory 注入 prompt”的步骤，只要模型选择调用 memory_search/memory_get，结果就会自动进入上下文。

### 2.3 工作区与 Bootstrap（OpenClaw 对齐）

- **默认工作区**：`~/.mw4agent/workspace`（可通过环境变量 `MW4AGENT_WORKSPACE_DIR` 覆盖）。首次使用时自动创建。
- **Agent**：Runner 的 tool context 使用 `params.workspace_dir or get_default_workspace_dir()`，Gateway 发起 run 时传入该默认工作区。
- **CLI**：`memory status/search/get` 的 `--workspace` 默认为 `~/.mw4agent/workspace`。
- **Bootstrap 注入**：每次 Agent run 前，从工作区按**固定顺序**读取以下文件（若存在），拼接后作为 **system prompt** 注入 LLM。顺序为 **IDENTITY.md → USER.md → MEMORY.md → memory.md** 优先，再读 AGENTS.md、SOUL.md、TOOLS.md、HEARTBEAT.md、BOOTSTRAP.md，这样在总字符上限内会优先包含“身份/用户/记忆”，避免“我是谁”等依赖 MEMORY.md 的问题。单文件与总字符数有上限（与 OpenClaw token-use 对齐）。

### 2.4 与 OpenClaw 文件格式兼容性

**可以。** MW4Agent 对 MEMORY.md、USER.md、IDENTITY.md 等 .md 文件的处理与 OpenClaw 兼容，可直接复用 OpenClaw 的 workspace 文件。

| 维度 | 说明 |
|------|------|
| **文件名与目录** | 与 OpenClaw 的 `VALID_BOOTSTRAP_NAMES` 对齐：根下支持 `AGENTS.md`、`SOUL.md`、`TOOLS.md`、`IDENTITY.md`、`USER.md`、`HEARTBEAT.md`、`BOOTSTRAP.md`、`MEMORY.md`、`memory.md`，以及 `memory/*.md`。目录结构为「工作区根目录 + 上述文件 + memory/ 子目录下 .md」。 |
| **文件内容格式** | **无特殊格式要求**：所有 .md 均按**纯文本**读写，不做 YAML frontmatter 解析或结构化解析。OpenClaw 的 MEMORY.md / USER.md 等若为纯 Markdown 或「YAML frontmatter + Markdown」，可直接使用；frontmatter 会一并注入/检索，不会报错。 |
| **工作区路径** | OpenClaw 默认工作区为 `~/.openclaw/workspace`，MW4Agent 为 `~/.mw4agent/workspace`。若要直接使用 OpenClaw 的同一批文件，可将 OpenClaw workspace 目录内容**复制或链接**到 `~/.mw4agent/workspace`，或将 `MW4AGENT_WORKSPACE_DIR` 指向 OpenClaw 的 workspace 目录。 |

**小结**：同一套 MEMORY.md、USER.md、IDENTITY.md 等可直接在 OpenClaw 与 MW4Agent 间共用，仅需保证路径指向同一目录或拷贝到 MW4Agent 默认工作区即可。

### 2.5 后续扩展

可在此之上增加：

- 会话文件源（如将会话日志落盘为 jsonl/md 并纳入 `list_memory_files` 或单独 source）；
- 向量索引与混合检索（embedding + FTS、MMR、时间衰减），使行为与 OpenClaw 的 Memory 系统（见 [docs/openclaw/memory.md](../../openclaw/memory.md) §2–§5）更一致。

---

## 3. 相关文档与代码

- 完整 Memory 系统（后端、配置、检索流程）：[docs/openclaw/memory.md](../../openclaw/memory.md)
- 实现位置：
  - `mw4agent/memory/`：list/search/read_file
  - `mw4agent/agents/tools/memory_tool.py`：memory_search、memory_get 工具
  - `mw4agent/cli/memory/register.py`：memory status/search/get 子命令
