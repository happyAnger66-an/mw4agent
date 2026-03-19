# Mem0 原理与架构（面向 mw4agent 集成）

本文基于上游开源项目 **mem0**（Python 包名 `mem0ai`）的代码结构做归纳。在本 monorepo 中，源码通常位于与 `mw4agent` 同级的 `mem0/` 目录（非 `mw4agent` 子目录）。

---

## 1. 定位与核心思想

Mem0 是面向 **个性化 / 长期记忆** 的中间层：在对话或 Agent 运行中，把原始消息经 **LLM 抽取事实**（或不经推理直接写入），配合 **向量检索**（及可选 **图存储**）实现「记住用户偏好、历史事实、多轮上下文外的稳定知识」。

与「整段上下文塞进 prompt」相比，其典型路径是：

1. **写入（add）**：消息 →（可选）LLM 提取 `facts` → 与已有记忆做 **合并 / 更新 / 删除** 决策（仍由 LLM）→ 写入向量库 + 元数据；可选并行写入图。
2. **读取（search）**：查询文本 → embedding → 向量相似度检索 + **会话维度过滤** →（可选）rerank → 返回结构化记忆列表。

**强依赖**：默认路径下需要 **LLM**（事实抽取与记忆更新策略）和 **Embedder**（向量）；向量库可换成本地或托管多种实现。

---

## 2. 模块与分层架构

### 2.1 入口类：`mem0.memory.main.Memory`

- 继承 `MemoryBase`（`mem0/memory/base.py`），但 `MemoryBase` 只定义 **按 ID 的 CRUD + history**；**对外主 API** 实际是 `Memory` 上的 `add` / `search` / `reset` 等。
- 构造时组装整条流水线（见 `Memory.__init__`）：
  - **EmbedderFactory** → `embedding_model`
  - **VectorStoreFactory** → `vector_store`
  - **LlmFactory** → `llm`
  - **SQLiteManager** → `db`（`history.db`，记录记忆的变更历史）
  - **RerankerFactory**（可选）→ `reranker`
  - **GraphStoreFactory**（当 `graph_store.config` 非空）→ `graph`，`enable_graph = True`

### 2.2 配置：`MemoryConfig`（`mem0/configs/base.py`）

聚合子配置：

| 字段 | 作用 |
|------|------|
| `vector_store` | 向量库 provider + 连接/collection 等 |
| `llm` | 事实抽取与更新策略所用 LLM |
| `embedder` | 向量化模型 |
| `history_db_path` | SQLite 历史库路径（默认 `~/.mem0/history.db` 或 `MEM0_DIR`） |
| `graph_store` | 可选图存储 |
| `reranker` | 可选检索后重排 |
| `custom_fact_extraction_prompt` / `custom_update_memory_prompt` | 覆盖默认提示词 |

### 2.3 可插拔工厂（`mem0/utils/factory.py`）

- **LlmFactory**：按 provider 名动态 import LLM 实现类；支持 `register_provider` 扩展。
- **EmbedderFactory** / **VectorStoreFactory** / **GraphStoreFactory** / **RerankerFactory**：同类模式。

### 2.4 抽象接口

- **向量库** `VectorStoreBase`（`mem0/vector_stores/base.py`）：`create_col`、`insert`、`search`、`update`、`delete`、`get`、`list`、`reset` 等。
- **Embedding** `EmbeddingBase`：`embed(text, memory_action)`，`memory_action` 为 `add` / `search` / `update`。
- **LLM** `LLMBase`：`generate_response` 等（各 provider 实现细节不同）。

### 2.5 本地历史：`SQLiteManager`（`mem0/memory/storage.py`）

- 维护 `history` 表：记忆 ID、旧/新内容、事件类型、时间戳、`actor_id`、`role` 等。
- 与向量库中的「当前记忆」配合，支持 `history(memory_id)` 等 API。

### 2.6 图记忆（可选）

- 当启用 graph 时，`add` 内与向量写入 **并行**（`ThreadPoolExecutor`）：`_add_to_vector_store` + `_add_to_graph`。
- `search` 同样并行查向量与 `graph.search`，返回中可带 `relations`。

### 2.7 其他仓库内组件（了解即可）

- **`server/`**：独立服务入口。
- **`openmemory/api/`**：OpenMemory 的 FastAPI、DB、路由等，偏产品与多租户场景。
- **`mem0/client/`**：托管 API 客户端。

---

## 3. 关键数据流

### 3.1 `add(messages, user_id=..., agent_id=..., run_id=..., infer=True, ...)`

1. **作用域校验**：`user_id` / `agent_id` / `run_id` **至少提供一个**，用于元数据与检索过滤（`_build_filters_and_metadata`）。
2. **消息格式**：支持 `str`、`dict` 或 `list[dict]`（OpenAI 风格 `role`/`content`）；可处理 vision（依赖 LLM 配置）。
3. **`infer=False`**：逐条消息直接 embedding + 写入向量库，不跑抽取 LLM。
4. **`infer=True`（默认）**：
   - 用（可自定义的）提示词 + LLM **JSON** 输出得到 `facts`；
   - 对每个新 fact 做 embedding，并在当前会话过滤条件下 **检索旧记忆**；
   - 再经 **update memory** 相关 LLM 调用决定 ADD / UPDATE / DELETE（具体逻辑在 `main.py` 后续部分）；
   - 同步更新 SQLite history。
5. **程序性记忆**：`memory_type == procedural_memory` 且带 `agent_id` 时走 `_create_procedural_memory` 分支。
6. **图**：若启用，并行写入关系数据。

### 3.2 `search(query, user_id|agent_id|run_id, limit, filters, threshold, rerank=True)`

1. 同样要求至少一个会话 ID；`filters` 支持简单等值或较丰富的操作符（部分需向量库能力支持），见源码 `_process_metadata_filters`。
2. 并行：向量检索 +（可选）图检索。
3. 若配置 reranker，对向量结果做 **query-document 重排**；失败则回退原排序。

### 3.3 会话与多主体模型

Mem0 用元数据字段区分：

- **`user_id` / `agent_id` / `run_id`**：会话或租户隔离（存储模板与查询 filter 一致注入）。
- **`actor_id`**：多角色对话中标识说话者（如 message `name` 映射），用于过滤与历史。

mw4agent 侧若有「用户 / Agent / 单次 run」，需要 **显式映射** 到上述字段，避免记忆串租。

---

## 4. 与 mw4agent 现有记忆抽象的对照

| 维度 | mw4agent（`mw4agent/memory/backend.py`） | Mem0 |
|------|------------------------------------------|------|
| 抽象 | `MemoryBackend`：`search` / `read_file` / `sync` / `note_session_delta` / `status` | `Memory`：`add` / `search` / CRUD / `history` |
| 检索输入 | `query` + `SearchOptions`（含 session、agent） | `query` + `user_id`/`agent_id`/`run_id` + filters |
| 检索输出 | `List[MemorySearchResult]`（path、行号、snippet） | `{"results": [{id, memory, score, metadata, ...}]}` |
| 写入 | 主要依赖工作区文件 + 可选 transcript 索引 | **主动 `add(messages)`**，由 LLM 提炼 fact |
| 读文件 | `read_file` 按路径读 workspace | Mem0 **无**等价物；记忆是原子「条目」而非文件切片 |

结论：**不是 drop-in 替换**。集成时要么实现适配器把 Mem0 的「条目」映射成伪 path/snippet，要么扩展工具层支持「mem 条目 ID」读取。

---

## 5. 集成到 mw4agent 建议预留的扩展接口

以下接口/约定建议在 mw4agent 中预留或逐步落地，便于接入 Mem0 且不破坏现有 Stub / LocalIndex 后端。

### 5.1 后端选择与配置命名空间

- 在根配置 `memory` 段增加 **`backend` 或 `provider`**（如 `stub` | `local_index` | `mem0`），与现有 **`enabled`** 语义对齐（可约定：`enabled=true` 且 `provider=mem0` 时走 Mem0）。
- 预留 **`memory.mem0`**（或 `memory.providers.mem0`）子树：承载 `MemoryConfig` 可序列化子集（vector_store / llm / embedder / graph_store / reranker / history_db_path / prompts），**密钥优先环境变量或密钥管理**，避免写明文进仓库。

### 5.2 `MemoryBackend` 的 Mem0 适配实现

- 新增 **`Mem0MemoryBackend`**（名称可自定）：
  - **`search`**：将 `SearchOptions.session_id` / `agent_id` / `session_key` **映射**为 Mem0 的 `run_id` / `agent_id` / `user_id`（映射策略写死在一处并文档化）；把返回的 `memory` 文本填入 `MemorySearchResult.snippet`，`path` 可用合成形式如 `mem0:<id>` 便于工具链一致。
  - **`read_file`**：若 `rel_path` 形如 `mem0:<id>`，可调用 Mem0 的 **`get(memory_id)`**（若公开 API 可用）；否则降级为「不支持」或继续走文件读。
  - **`sync`**：可映射为 **批量重索引/对账**（若使用文件导入管线）或 no-op（Mem0 以 `add` 为增量源）。
  - **`note_session_delta`**：在 transcript 更新后调用 **`memory.add(messages_slice, ..., infer=...)`**，或节流批量写入；需定义 **切片策略**（条数、token、时间窗口）。
  - **`status`**：返回 Mem0 健康信息（向量库连通、上次 add 时间、配置 provider 名等）。

### 5.3 `get_memory_backend()` 工厂扩展

- 将「仅 `enabled` + LocalIndex」扩展为 **多后端分支**，支持从配置构造 `Mem0MemoryBackend`（单例或按 agent 多实例，视隔离需求而定）。

### 5.4 LLM / Embedding 复用策略（预留钩子）

- Mem0 默认自带一套 LLM 与 Embedder 配置；mw4agent 已有对话模型时，可选：
  - **独立配置**：Mem0 使用单独小模型（成本低、延迟可控）；
  - **复用**：预留 **从 mw4agent 注入 client 或 provider 配置** 的工厂钩子（若不愿 fork mem0，可通过 Mem0 支持的 `litellm` / 环境变量对齐同一 API base）。

建议在文档与配置中预留字段：`memory.mem0.llm_profile` / `reuse_agent_llm: bool`（具体实现可后置）。

### 5.5 工具层（`memory_tool`）行为开关

- **`memory_search`**：当后端为 Mem0 时，结果结构仍宜保持现有 JSON schema（`results[]` 含 path、snippet、score），由适配器填充；可选增加字段 `memory_id`、`source: "mem0"`（若需前端或 agent 区分）。
- **写入路径**：Mem0 强调 **`add`**；若产品上要「只读检索、不写记忆」，需在配置增加 **`mem0.read_only`** 并在 `note_session_delta` 中短路。

### 5.6 生命周期与可靠性

- **异步**：Mem0 的 `add` / `search` 多为同步阻塞；mw4agent 若在 async 工具中调用，预留 **线程池或 asyncio.to_thread** 包装，避免阻塞事件循环。
- **降级**：Mem0 或向量库失败时，可配置 **回退到 LocalIndexBackend / Stub**（在 `get_memory_backend` 或适配器内 try/except）。
- **遥测**：Mem0 内置 telemetry 事件；企业环境可通过环境变量关闭（见上游文档），配置中预留 **`mem0.telemetry_enabled`** 文档说明即可。

### 5.7 进程与部署形态

- **进程内**：直接 `from mem0 import Memory`，与 agent 同进程（简单，需注意依赖体积与原生库）。
- **进程外**：通过 Mem0 **server / OpenMemory HTTP** 访问时，预留 **`MemoryBackend` 的 HTTP 客户端实现**（与 `Mem0MemoryBackend` 并列），配置 `base_url` + API key。

---

## 6. 小结

- **Mem0** = 配置驱动的 **LLM 事实抽取 + 向量检索（+ 可选图 + rerank）+ SQLite 历史**，以 **`user_id` / `agent_id` / `run_id`** 做隔离。
- **mw4agent** 当前 **MemoryBackend** 以 **工作区文件 + 可选 SQLite 全文式索引** 为中心；接入 Mem0 需要 **适配器、ID 映射、增量写入策略**，并在配置与工厂层预留 **provider 分支** 与 **可选 HTTP 后端**。

后续若落地实现，建议先只做 **`search` 只读 + 明确 ID 映射**，再开 **`note_session_delta` → add** 的异步节流写入，最后视需求接 **图 / rerank**。
