# MW4Agent MemoryIndex 实现记录（阶段性落地）

本文用于记录 `docs/do_plan.md` 中 Memory 相关规划的**实际实现进度**，方便之后查阅“做到哪一步了、代码在哪儿、行为有何变化”。

---

## Phase 0：MemoryBackend 抽象（已完成）

- **时间**：已完成于本次迭代。
- **核心改动**：
  - 新增 `mw4agent/memory/backend.py`：
    - 定义 `SearchOptions` 数据类（`max_results/min_score/session_key/session_id/agent_id`）。
    - 定义抽象类 `MemoryBackend`：
      - `search(...)` / `read_file(...)` / 预留 `sync` / `note_session_delta` / `status`。
    - 实现 `StubMemoryBackend`：
      - `search(...)` 直接委托到现有 `mw4agent.memory.search.search(...)`。
      - `read_file(...)` 委托到 `mw4agent.memory.search.read_file(...)`。
    - 工厂方法 `get_memory_backend()` 返回单例 `StubMemoryBackend`。
  - `MemorySearchTool` / `MemoryGetTool`（`mw4agent/agents/tools/memory_tool.py`）改为通过 backend 调用：
    - 构造 `SearchOptions`，从 `tool_context` 里带上 `session_id/agent_id/session_key` 等。
    - 调用 `backend.search(...)` / `backend.read_file(...)`，不再直接引用 `memory.search/read_file`。
- **行为影响**：
  - 对用户完全透明：未引入 index/embedding 时，`memory_search` / `memory_get` 的行为与返回结果保持 100% 一致。

---

## Phase 1：本地 SQLite MemoryIndex（仅文件源，FTS-like 搜索）（已完成）

- **时间**：已完成于本次迭代。

### 1. 新增 SQLite 索引层：`mw4agent/memory/index.py`

- 主要职责：
  - 管理每个 agent 的本地索引文件：`~/.mw4agent/agents/<agentId>/memory/index.sqlite`。
  - 为 file-based memory 源（`MEMORY.md` + `memory/*.md` 等）提供统一的 on-disk 索引。
- 核心结构：
  - `chunks` 表：
    - `id INTEGER PRIMARY KEY AUTOINCREMENT`
    - `source TEXT`（当前只用 `"memory"`）
    - `path TEXT`（如 `MEMORY.md`、`memory/notes.md`）
    - `content TEXT`（整文件内容）
    - `created_at/updated_at INTEGER`（毫秒时间戳）
- 索引构建：
  - `index_files(db_path, workspace_dir, sources=("memory",))`：
    - 扫描 workspace 下的 bootstrap/memory 文件：
      - 根级：`AGENTS.md/SOUL.md/TOOLS.md/IDENTITY.md/USER.md/HEARTBEAT.md/BOOTSTRAP.md/MEMORY.md/memory.md`
      - 目录：`memory/*.md`
    - 先 `DELETE FROM chunks WHERE source="memory"`，再重新插入所有文件内容。
- 搜索实现：
  - `search_index(db_path, query, max_results, min_score)`：
    - 本地 `_normalize_query_local` 做关键字分词（对 CJK 做 bigram 拆分，逻辑与原 `memory.search` 一致但复制在本文件内避免循环依赖）。
    - 构造 `content LIKE ? OR content LIKE ? ...` 的简单 SQL 查询，读取匹配行。
    - 返回简化的字典列表：
      - `{"path", "start_line", "end_line", "score", "snippet", "source"}`
    - 当前 snippet 取整文件前 ~500 字符，`start_line/end_line` 先固定为 1（后续可细化为按行截断）。

### 2. LocalIndexBackend：基于 SQLite 的 MemoryBackend 实现

- 文件：`mw4agent/memory/backend.py`
- 行为：
  - `_db_path_for(agent_id)`：
    - 使用 `resolve_agent_dir(agent_id)` → `<agentDir>/memory/index.sqlite`。
  - `_ensure_index(agent_id, workspace_dir)`：
    - 维护一个 `_indexed_workspaces: set[(agent_id, workspace_dir)]`。
    - 若尚未索引，则调用 `index_files(...)` 重建 `"memory"` 源的索引，然后记入 set。
  - `search(...)`：
    - 调用 `search_index(...)` 获取字典结果。
    - 将每条结果包装为 `MemorySearchResult`（保持与旧 API 兼容）：
      - `path/start_line/end_line/score/snippet/source`。
  - `read_file(...)`：
    - 为保持行为一致，仍直接委托给 `memory.search.read_file(...)`，不从 SQLite 读 raw 文本。

### 3. backend 选择逻辑：`get_memory_backend()`

- 从 root config 段 `memory` 中读取：
  - 若存在 `{"enabled": true}` 且为布尔 `true`：
    - 返回 `LocalIndexBackend(memory_cfg=cfg)`。
  - 否则：
    - 返回 `StubMemoryBackend()`（Phase 0 的文件版实现）。
- 这意味着：
  - **默认行为不变**：未显式开启 `memory.enabled` 时，仍然使用原始文件扫描。
  - 需要体验本地 MemoryIndex 时，只需在 `~/.mw4agent/mw4agent.json` 中加入：
    ```json
    {
      "memory": {
        "enabled": true
      }
    }
    ```

### 4. 测试与回归验证

- 新增测试：`tests/test_memory_index_backend.py`
  - 在临时目录下设置：
    - `MW4AGENT_STATE_DIR` 指向临时 `.mw4agent`。
    - `MW4AGENT_CONFIG_DIR` 指向临时 `cfg` 目录，并写入：
      ```json
      {
        "memory": {
          "enabled": true
        }
      }
      ```
  - 创建临时 workspace `ws/MEMORY.md`，写入 `"hello index backend"`。
  - 调用：
    - `backend = get_memory_backend()` → 断言为 `LocalIndexBackend`。
    - `backend.search("hello", str(ws), options=SearchOptions(...))`，断言结果中包含 `"MEMORY.md"`。
- 全量回归：
  - `pytest` 全通过，说明：
    - 当未设置 `memory.enabled` 时，行为与旧版完全一致；
    - 开启 `memory.enabled=true` 也不会破坏现有 E2E/单元测试逻辑。

---

## Phase 2：会话增量同步 + sessions 纳入索引（已完成）

- **时间**：本次迭代补全（对齐 `docs/do_plan.md` Phase 2）。
- **Transcript → MemoryIndex**（`mw4agent/agents/session/transcript.py`）：
  - 在 `append_messages` / `append_compaction` / `append_custom` / `branch_to_parent` 写入结束后调用 `_notify_transcript_index_delta(...)`。
  - 内部 **惰性** `get_memory_backend().note_session_delta(...)`，并据 transcript 路径推断 `agent_id`（`.../agents/<id>/sessions/...`）。
  - **Runner**（`mw4agent/agents/runner/runner.py`）不再重复调用 `note_session_delta`，避免与 transcript 钩子双重触发。
- **可配置节流**（`memory.sync.sessions`）：
  - `LocalIndexBackend` 读取 `deltaBytes` / `deltaMessages`（非负整数）。
  - **二者均为 0 或未配置**（默认）：每次 `note_session_delta` 都尝试刷新会话 chunk（与 Phase 2 前行为一致）。
  - **任一大于 0**：累积增量，满足 **字节阈值或消息阈值** 之一时才刷新；未刷新时 **`memory_search` 仍会在带 `session_id` 的 search 路径上按文件 mtime 懒更新**，避免漏检。
- **检索元数据**：
  - `mw4agent/memory/index.py` 的 `search_index` 结果增加 `session_id`（由 `sessions/<sid>.jsonl` 解析）、`created_at` / `updated_at`（毫秒）。
  - `MemorySearchResult`（`mw4agent/memory/search.py`）增加对应可选字段。
  - `MemorySearchTool` JSON 增加可选 `sessionId` / `createdAt` / `updatedAt`（camelCase）。
- **`LocalIndexBackend.sync`**：
  - 清空已索引 workspace 集合、session mtime 缓存与 delta 累积表；下次 `search` 会按需重建文件侧索引。
- **循环依赖修复**：
  - `memory/search.py` 对 `transcript` 的引用改为 `_session_transcript_helpers()` 惰性导入，避免 `memory → agents → tools → memory.backend → memory.search` 初始化死锁。
- **测试**（`tests/test_memory_index_backend.py`）：
  - `autouse` fixture：`reset_memory_backend_singleton()`，避免单例污染。
  - 覆盖：会话命中含 `session_id` 与时间戳、`search_index` 元数据、**高阈值下 eager 不写 session 行但 search 仍能命中**、`sync` 后 workspace 文件变更可被重新索引。

---

后续 Phase 3（Embedding + 混合检索 + MMR/时间衰减）将在该索引与 backend 基础上继续迭代。  
本文件后续也可按阶段追加实现记录，方便查阅具体 commit/文件位置。
