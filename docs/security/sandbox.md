# Shannon Sandbox 执行原理与安全边界

本文基于姊妹仓库 **Shannon**（与 mw4agent 同级的 `Shannon/`）中 **agent-core（Rust）** 与 **llm-service（Python）** 的实现，说明 Shannon 所称「WASI 隔离工作区」类沙箱的**职责划分、创建方式、请求级执行流程与生命周期**，便于与 mw4agent 编排/工具链做安全对照。

> **范围说明**：Shannon 的「沙箱」在代码里主要体现为两层——(1) 面向 Agent 工具的 **gRPC `SandboxService`**（会话目录隔离 + 安全命令执行）；(2) 可选的 **`WasmSandbox`**（Wasmtime 执行 WASM / 子进程 + rlimit）。二者目标不同，下文分开描述。

---

## 1. 总体架构

| 组件 | 作用 |
|------|------|
| **`sandbox.proto`** | 定义 `SandboxService` RPC：读/写/列目录、受限命令、`file_search` / `file_edit` / `file_delete` 等，路径相对于会话工作区或 `/memory/`（见 proto 注释）。 |
| **`SandboxServiceImpl`**（`rust/agent-core/src/sandbox_service.rs`） | 在 **宿主机进程** 内实现上述 RPC：`WorkspaceManager` 绑定 `session_id` → 目录；`MemoryManager` 绑定 `user_id` → 持久记忆目录；文件与命令均在解析后的真实路径上操作。 |
| **`WorkspaceManager`**（`workspace.rs`） | **懒创建** 每会话独立目录，校验 `session_id` 与路径穿越；提供配额统计、`delete_workspace` 回收。 |
| **`SafeCommand`**（`safe_commands.rs`） | 将用户输入的「命令字符串」解析为 **白名单子命令**（`ls`、`cat`、`grep` 等），**禁止 shell 元字符**，在 Rust 内实现逻辑而非 `sh -c`，避免注入。 |
| **`SandboxClient`**（`python/llm-service/.../sandbox_client.py`） | 当 `SHANNON_USE_WASI_SANDBOX=1` 时，Python 侧文件类工具通过 gRPC 调用 `SandboxService`，而非直接操作本地文件系统。 |
| **`WasmSandbox`**（`sandbox.rs`） | 独立能力：Wasmtime + fuel、可选子进程 + Linux `rlimit`、临时目录映射的 `SandboxedFs`；与 gRPC 文件服务是 **并行存在的另一条隔离路径**（主要用于工具/WASM 执行，非会话目录 RPC 的主体）。 |

**agent-core 进程**：在 `main.rs` 中与 `AgentService` 一同监听 `0.0.0.0:50051`，并挂载 `SandboxService`。

---

## 2. 会话工作区如何「创建」——并非独立 VM

`SandboxService` **不会**为每个 RPC 单独 fork 微虚拟机；**隔离单元是会话目录**：

1. 环境变量 **`SHANNON_SESSION_WORKSPACES_DIR`**（默认 `/tmp/shannon-sessions`）作为所有会话工作区的根。
2. 首次对某 `session_id` 发起需要工作区的操作时，`WorkspaceManager::get_workspace(session_id)`：
   - 校验 `session_id`：非空、长度 ≤128、仅 `[A-Za-z0-9_-]`、禁止 `..` 与 `.` 开头等。
   - 将根目录 **canonicalize**，再 `join(session_id)`，创建目录（若不存在），并拒绝 **工作区根为指向外部的符号链接** 等异常。
   - 创建后再次 **canonicalize**，确认仍在根下（缓解 TOCTOU）。

因此：**「创建 sandbox目录」= 在配置的根下为该会话建一块专属磁盘命名空间**，供后续 `file_*` 与 `execute_command` 使用。

用户持久 **`/memory/`** 路径则由 **`SHANNON_MEMORY_DIR`**（默认 `/tmp/shannon-memory`）+ 消毒后的 `user_id` 子目录实现（OSS 版 `MemoryManager` 为简化实现）。

---

## 3. RPC 执行原理与安全校验

### 3.1 路径解析（`resolve_path`）

- **工作区路径**：客户端可使用相对路径，或带 `/workspace/` 前缀（与 Firecracker 等虚拟机内路径约定对齐后会被剥掉前缀），最终必须落在当前 `session_id` 的 canonical 工作区内；**禁止未规范化的绝对路径**穿越。
- **`/memory/`**：必须带非空 `user_id`；子路径禁止包含 `..`；已存在路径需 **canonicalize** 后仍位于用户记忆根下。

### 3.2 配额（`SandboxConfig` 默认）

- 单次读上限、工作区总字节上限、每用户 memory 上限、命令超时下界等由 `SandboxConfig` 控制（如默认单次读 10MB、工作区 100MB、memory 10MB、命令默认 30s）。

### 3.3 写路径与符号链接（`file_write`）

对非 `/memory/` 的写入：在创建父目录前 **逐段校验路径分量**，禁止 `..`、`ParentDir` 跳出，并对已存在分量上的 **符号链接** 做解析，确保目标仍在工作区内（减轻 symlink 逃逸）。

### 3.4 命令执行（`execute_command`）

1. `SafeCommand::parse` 失败 → 返回「命令不允许」（例如含 `|`、`;``、反引号等或不在白名单）。
2. 若解析后的命令需访问 `/memory`，则要求 **`user_id` 非空** 并解析记忆目录。
3. 在 **`spawn_blocking`** 中于会话工作区根执行 **Rust 实现的等价命令**（非任意 shell），外层再用 **`tokio::time::timeout`** 限制 wall time（请求超时与配置上限取小，proto 中约定最大约 30s 量级）。

### 3.5 其它 RPC

- **`file_search`**：限制查询长度、上下文行数、扫描文件数/单文件大小、跳过常见二进制扩展名等，降低 DoS 面。
- **`file_delete`**：**明确禁止**删除 `/memory/` 下内容；支持 glob 删除时对目录符号链接做 canonical 检查以防逃逸。

---

## 4. `WasmSandbox` 与 gRPC 沙箱的关系

`WasmSandbox` 提供：

- **WASM**：Wasmtime，启用 fuel metering、内存 guard、模块缓存；`execute_wasm_internal` 对模块大小与魔数校验。
- **本机工具二进制**：`execute_tool` 在 Linux 上对子进程设置 **RLIMIT_CPU / AS / NOFILE / NPROC**（`pre_exec`），并做 wall-clock 超时。
- **临时文件视图**：`create_fs_sandbox` 使用 `tempfile::TempDir`，随 `SandboxedFs` 析构删除。

这与 **`SandboxService` 的「按 session 持久目录」** 是不同用途：**proto 层沙箱**侧重点是 **多租户目录边界 + 安全命令**；**WasmSandbox** 更偏向 **单次执行的计算资源束缚**（当前 agent-core 主入口将其作为库能力保留，未必与每条工具调用一一对应）。

---

## 5. 生命周期管理

| 阶段 | 行为 |
|------|------|
| **创建** | 首次 `get_workspace` 成功时在 `SHANNON_SESSION_WORKSPACES_DIR/<session_id>` 建目录；无单独 RPC「申请 sandbox」。 |
| **使用期** | 该 `session_id` 下所有文件/命令 RPC 共享同一目录；`memory` 按 `user_id` 跨会话持久。 |
| **读/写/搜索/删除** | 每次 RPC 独立；命令执行受单次超时约束，**无**长期子进程池。 |
| **回收** | `WorkspaceManager::delete_workspace(session_id)` 可 `remove_dir_all` 会话目录（**需编排层或运维在会话结束时调用**；`SandboxService` 的 proto **未**暴露删除会话的 RPC，回收策略取决于上层产品）。 |
| **Python 开关** | `SHANNON_USE_WASI_SANDBOX=1` 时走 gRPC；否则工具可能回退本地实现（见 `sandbox_client.py` 模块注释）。 |

---

## 6. 与 mw4agent 的简要对照

- Shannon 工具链通过 **稳定的 session_id + 可选 user_id** 将文件与「安全 shell」约束在 agent-core 一侧。
- mw4agent Gateway/桌面编排若对接 Shannon 工具，应 **沿用相同会话键与记忆键**，并在产品层定义 **会话结束何时调用 `delete_workspace`**（或等价清理），避免磁盘与工作区配额长期堆积。

---

## 7. 源码索引（Shannon 仓库）

| 主题 | 路径 |
|------|------|
| RPC 定义 | `protos/sandbox/sandbox.proto` |
| gRPC 实现 | `rust/agent-core/src/sandbox_service.rs` |
| 会话目录 | `rust/agent-core/src/workspace.rs` |
| 安全命令 | `rust/agent-core/src/safe_commands.rs` |
| 用户记忆目录 | `rust/agent-core/src/memory_manager.rs` |
| Wasm/rlimit 沙箱 | `rust/agent-core/src/sandbox.rs` |
| 服务入口 | `rust/agent-core/src/main.rs` |
| Python 客户端 | `python/llm-service/llm_service/tools/builtin/sandbox_client.py` |

---

## 8. 参考

- mw4agent 侧 Shannon 编排索引：`mw4agent/docs/orchestrating_shannon.md`
