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
| **mw4agent.memory** | 模块：`list_memory_files(workspace_dir)`、`search(query, workspace_dir, max_results=10, min_score=0)`、`read_file(workspace_dir, rel_path, from_line=None, lines=None)`。来源仅限工作区 `MEMORY.md`、`memory.md`、`memory/*.md`。检索为关键词匹配（无 embedding）。 |
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

### 2.3 工作区与上下文

- **Agent**：通过 Runner 的 tool context 传入 `workspace_dir`（来自 `AgentRunParams.workspace_dir` 或当前工作目录）。
- **CLI**：通过 `--workspace` 指定目录，默认为当前目录。

### 2.4 后续扩展

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
