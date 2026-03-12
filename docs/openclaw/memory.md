# OpenClaw Memory 系统架构与原理（含长短期记忆）

本文基于 `openclaw` 仓库的源码，对其 Memory 系统（向量检索 + 会话记忆）进行整体梳理，重点说明：

- Memory 在 OpenClaw 中的角色与整体架构；
- 向量索引（长期记忆）和会话文件（短期记忆）的组织方式；
- 搜索流程（混合检索、MMR、时间衰减等）；
- 多后端（本地 SQLite / 远程 QMD）与 fallback 机制。

---

## 1. Memory 在 OpenClaw 中的角色

OpenClaw 的 Memory 子系统为 Agent 提供“代码+文档+会话”的长期/短期记忆检索能力，主要用于：

- 从代码仓库、笔记、文档等中搜索与当前问题相关的片段（长期记忆）；
- 从最近的会话记录里提取上下文（短期记忆）；
- 为 Agent 工具（如 `memory_tool`）和 CLI（`memory-cli`）提供统一的查询接口。

对外暴露的核心入口：

- `src/memory/index.ts`：
  - `MemoryIndexManager`：内置 SQLite 向量索引管理器；
  - 类型 `MemorySearchManager` / `MemorySearchResult`；
  - `getMemorySearchManager` / `closeAllMemorySearchManagers`（来自 `search-manager.ts`）。
- `src/agents/memory-search.ts`（未在此文展开）：
  - 将 Memory 搜索能力挂接到 Agent 侧的工具/逻辑中。

---

## 2. 后端与管理器：MemoryIndexManager & MemorySearchManager

### 2.1 MemoryIndexManager：内置 SQLite 向量索引

文件：`src/memory/manager.ts`

`MemoryIndexManager` 是内置“文件+会话”向量索引的主实现，核心特性：

- **单例缓存**：
  - 通过 `INDEX_CACHE` / `INDEX_CACHE_PENDING` 按 `agentId + workspaceDir + settings` 做缓存；
  - `MemoryIndexManager.get({ cfg, agentId, purpose })` 负责创建或复用实例。

- **配置与上下文**：
  - `cfg: OpenClawConfig`：全局配置；
  - `agentId`：当前智能体 ID；
  - `workspaceDir`：`resolveAgentWorkspaceDir(cfg, agentId)` 计算得到；
  - `settings: ResolvedMemorySearchConfig`：从 `agents.memorySearch` 配置解析而来（见 §3）。

- **向量与 FTS 存储**：
  - SQLite 表：
    - `chunks_vec`：向量表（`VECTOR_TABLE`）；
    - `chunks_fts`：全文检索表（`FTS_TABLE`）；
    - `embedding_cache`：向量缓存表（`EMBEDDING_CACHE_TABLE`）。
  - `vector` 配置：
    - 是否启用向量扩展、扩展路径、维度信息等；
  - `fts` 配置：
    - 是否启用 FTS、加载错误等。

- **Embedding Provider 多模型支持**：
  - 通过 `createEmbeddingProvider` 创建嵌入提供方：
    - OpenAI / Gemini / Voyage / Mistral / Ollama / 本地模型 / Auto；
  - 保存 `provider`、`openAi/gemini/voyage/...` 客户端实例；
  - 支持 fallback（例如从远程退回本地）：
    - `fallbackFrom` / `fallbackReason` / `providerUnavailableReason`。

- **文件与会话源**：
  - `sources: Set<MemorySource>`：
    - `"memory"`：长期记忆（例如 docs/、源码）；
    - `"sessions"`：短期记忆（会话文件）。
  - `extraPaths`：
    - 从配置中补充的额外目录（如知识库路径）。

- **同步与监听**：
  - `sync(...)`：
    - 根据 `sync.onSessionStart` / `sync.onSearch` / `sync.watch` / `sync.intervalMinutes` 等设置触发；
    - 定时或按需扫描文件和会话日志，更新 SQLite 索引。
  - 文件监听：
    - `ensureWatcher()` + `FSWatcher` 监控工作区和额外路径。
  - 会话监听：
    - `ensureSessionListener()` + `sessionDeltas` 等结构，按 `sync.sessions.deltaBytes/deltaMessages` 触发会话文件写入索引。

### 2.2 MemorySearchManager 与多后端抽象

文件：`src/memory/search-manager.ts`

- `getMemorySearchManager({ cfg, agentId, purpose })`：
  - 首先通过 `resolveMemoryBackendConfig` 判断使用哪种 backend：
    - 若 backend 为 `"qmd"` 且配置可用：
      - 用 `QmdMemoryManager` 创建远程后端；
      - 再用 `FallbackMemoryManager` 包装，为 QMD 提供一个“远程 + 本地内置索引”的双层结构；
    - 否则退回到内置 `MemoryIndexManager`。
- `FallbackMemoryManager`：
  - 首先尝试 primary（如 QMD 远程向量存储）；
  - primary 出错后：
    - 记录错误；
    - 关闭 primary；
    - 删除缓存，使下一次可以重新尝试；
    - 懒加载 builtin fallback（`MemoryIndexManager`）；
  - 对外暴露统一的 `search/readFile/status/sync/probeEmbeddingAvailability` 等接口。

**这套后端抽象，允许 Memory 同时支持：**

- 本地 SQLite + 向量扩展的内置索引（默认）；
- 远程向量存储（QMD 后端），并在失败时平滑回退到本地索引。

---

## 3. 配置与长/短期记忆来源：ResolvedMemorySearchConfig

文件：`src/memory/backend-config.ts`

`ResolvedMemorySearchConfig` 是 Memory 搜索的“总配置”，从全局 `OpenClawConfig` 中解析而来，主要字段：

- **基本开关与来源**：

  - `enabled: boolean`：整体是否启用 Memory；
  - `sources: Array<"memory" | "sessions">`：
    - 通过 `normalizeSources` 将用户配置与 `experimental.sessionMemory` 组合：
      - 当开启 `sessionMemory` 时，`"sessions"` 才会被纳入 sources；
  - `extraPaths: string[]`：额外记忆路径。

- **后端与模型**：

  - `provider`: `"openai" | "local" | "gemini" | "voyage" | "mistral" | "ollama" | "auto"`；
  - `remote`：
    - `baseUrl` / `apiKey` / `headers`；
    - `batch` 配置：是否启用批量、并发数、轮询间隔、超时等；
  - `fallback`: `"openai" | "gemini" | "local" | "voyage" | "mistral" | "ollama" | "none"`；
  - `model`: 具体模型名，若未指定则按 provider 选择默认模型（如 `text-embedding-3-small`）。

- **存储与切片**：

  - `store`：
    - `driver: "sqlite"`；
    - `path`: 存储路径，支持 `{agentId}` 模板，占位由 `resolveStorePath` 展开到 state 目录；
    - `vector`: 是否启用向量表、扩展路径等。
  - `chunking`：
    - `tokens` / `overlap`：切片 token 长度与重叠；
    - 通过 clamp 逻辑保证范围合法。

- **同步策略（短期记忆的关键）**：

  - `sync`：
    - `onSessionStart` / `onSearch`：是否在会话开始或搜索时触发索引同步；
    - `watch` / `watchDebounceMs`：是否实时监听文件变化；
    - `intervalMinutes`：周期同步；
    - `sessions`：
      - `deltaBytes`：会话文件累计字节变化超过此值触发同步；
      - `deltaMessages`：会话消息数超过此值触发同步。

- **检索与打分**：

  - `query`：
    - `maxResults` / `minScore`；
    - `hybrid`：
      - `enabled`：是否启用混合检索；
      - `vectorWeight` / `textWeight` / `candidateMultiplier`；
      - `mmr`（Maximal Marginal Relevance）：
        - `enabled` / `lambda`；
      - `temporalDecay`：
        - `enabled` / `halfLifeDays`（时间衰减半衰期）。
  - `cache`：
    - 是否启用 embedding 缓存及最大条数。

> 其中，`sources` + `experimental.sessionMemory` 就是“长/短期记忆来源”的显式配置：
>
> - `"memory"` → 长期记忆：代码、文档等文件；
> - `"sessions"` → 短期记忆：会话日志文件，以 deltaBytes/deltaMessages 方式增量写入索引。

---

## 4. 检索流程：混合检索、MMR 与时间衰减

### 4.1 search：短/长期记忆统一入口

`MemoryIndexManager.search(query, opts)` 是 Memory 检索的统一入口（见 `manager.ts`）：

1. **会话预热与同步**：
   - `warmSession(sessionKey)`：在配置 `sync.onSessionStart` 时，对当前会话做一次 sync，以保证会话记忆尽量最新；
   - 若 `sync.onSearch` 且 `dirty || sessionsDirty`，则再触发一次 sync（原因标记为 `"search"`）。

2. **清洗查询**：
   - `cleaned = query.trim()`，空查询直接返回空结果。

3. **确定打分与返回上限**：
   - `minScore`：来自配置或调用参数；
   - `maxResults`：同上；
   - `candidates`：`min(200, max(1, floor(maxResults * hybrid.candidateMultiplier)))`。

4. **仅 FTS 模式（无 embedding provider）**：
   - 当 `!provider` 且 `fts.enabled && fts.available` 时：
     - 先 `extractKeywords`（提取关键词）；
     - 对每个关键词进行 FTS 搜索 `searchKeyword`，得到多个结果集；
     - 按 chunk ID 聚合，保留最高得分，然后排序、过滤、截断；
   - 这相当于**只使用“短语/关键词检索”**的记忆模式。

5. **混合模式（embedding + FTS）**：
   - 若开启了 hybrid 且 FTS 可用：
     - `keywordResults = searchKeyword(cleaned, candidates)`；
   - 向量检索：
     - `queryVec = embedQueryWithTimeout(cleaned)`；
     - 若向量全零，则跳过；否则 `vectorResults = searchVector(queryVec, candidates)`；
   - 若 hybrid 未启用或 FTS 不可用，仅返回向量结果：
     - `vectorResults.filter(score >= minScore).slice(0, maxResults)`；
   - 否则调用 `mergeHybridResults`：
     - 将 `vectorResults` 与 `keywordResults` 统一映射为：
       - `vectorScore` / `textScore`；
       - 通过 `vectorWeight` / `textWeight` / `mmr` / `temporalDecay` 综合打分；
     - 首先用 `minScore` 过滤，若有结果或 `keywordResults` 为空则直接返回；
     - 若没有严格满足 `minScore` 的结果，但存在 keyword-only 命中：
       - 放宽门槛为 `relaxedMinScore = min(minScore, textWeight)`；
       - 只保留与 keyword 结果相同 chunk 的条目，以避免丢掉仅 FTS 能抓到的精确匹配。

### 4.2 MMR 与时间衰减：去冗余与近期优先

在 `mergeHybridResults` 的实现中（委托给 `src/memory/hybrid.ts`）：

- **MMR（Maximal Marginal Relevance）**：
  - 通过 `mmr.enabled` / `lambda` 控制；
  - 目标是在相似度与多样性之间平衡，避免返回大量“非常相似”的片段。

- **时间衰减（Temporal Decay）**：
  - 通过 `temporalDecay.enabled` / `halfLifeDays` 控制；
  - 使得**较新的记忆片段在同等语义相似度下得分更高**，从而自然地体现“短期记忆权重更高”的效果。

这两者叠加，使得 Memory 系统既能记住长期知识（代码、文档），又能优先返回最近的、与当前问题高度相关的上下文（会话或近期改动的文件片段）。

---

## 5. 长期记忆 vs 短期记忆：实现视角总结

从实现角度看，OpenClaw 并没有把“长/短期记忆”拆成完全不同的系统，而是通过**同一个 MemoryIndexManager + 配置/源的区分**来实现：

- **长期记忆（Long-term Memory）**
  - 来源：`sources` 中的 `"memory"`，对应工作区中的代码、文档、笔记等；
  - 存储：SQLite 索引文件（默认在 state 目录 `memory/{agentId}.sqlite`）；
  - 同步：以文件监听与周期同步为主，不太频繁；
  - 检索：embedding + FTS 混合，结合 MMR/时间衰减。

- **短期记忆（Short-term / Session Memory）**
  - 来源：`sources` 中的 `"sessions"` + `experimental.sessionMemory: true`；
  - 表现为单独的会话文件（通常是日志/对话记录），同样被切片并写入同一 SQLite 索引；
  - 同步：
    - `sync.sessions.deltaBytes` / `deltaMessages` 控制增量同步频率；
    - `onSessionStart` / `onSearch` 可在会话开始或搜索时强制刷新；
  - 检索：
    - 仍然走统一的 search 流程，但在混合打分 + 时间衰减加持下，**与当前 session 相关的最近消息自然会排行靠前**。

换句话说：

- **长期记忆**更多通过“文件源 + embedding”显式提供；
- **短期记忆**则通过“会话文件 + 时间衰减 +同步策略”隐式融入同一索引；
- Agent 侧对 Memory 的使用是统一的，不需要区分调用接口，只在配置层决定要不要让哪些来源参与检索。

---

## 6. 与 MW4Agent 的对接启示

在 MW4Agent 中，要对齐或借鉴 OpenClaw 的 Memory 系统，可以考虑：

- 采用类似的 `MemorySearchManager` 抽象：
  - 支持本地 SQLite + 远程后端（如 QMD）双栈；
  - 用 Fallback 管理“远程挂掉 → 本地退回”逻辑。
- 配置级别区分长/短期记忆来源：
  - `"memory"`：代码/文档路径；
  - `"sessions"`：Agent 会话日志文件。
- 检索策略上应用：
  - 混合检索（embedding + FTS）；
  - MMR 去冗余；
  - 时间衰减（用更新时间/会话时间戳）。

这样可以在 Python 侧实现一个结构类似的 Memory 子系统，为 MW4Agent 的 Feishu 通道、Gateway 和 Agent 工具提供更强的“上下文记忆”能力。

