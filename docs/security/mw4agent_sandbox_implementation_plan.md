# mw4agent Sandbox 实施计划（对比 OpenClaw 与 Shannon）

本文对比 **workspace 内 OpenClaw 文档**（`docs/openclaw/sandbox.md`）所描述的沙箱与 **Shannon agent-core**（`docs/security/sandbox.md`）所描述的沙箱，并基于 mw4agent 现状给出 **分阶段、可落地的实施计划**。

---

## 1. 两套「Sandbox」解决的是不同问题

| 维度 | OpenClaw（文档中的 Sandbox） | Shannon（agent-core） |
|------|------------------------------|------------------------|
| **本质** | **策略层**：在已有权限管道之上，对「本轮 run 暴露给 LLM 的工具集合」再做一次 allow/deny 裁剪（`SandboxToolPolicy`）。 | **执行/隔离层**：会话级磁盘目录 + 路径解析与配额 + **非 shell** 的安全命令实现（gRPC）；可选 Wasm/rlimit。 |
| **隔离对象** | **工具名**（模型能调用哪些 API），不定义文件落在哪个物理目录。 | **路径与进程**：文件必须落在 `session_id` 工作区；命令只能是白名单子集且在超时内执行。 |
| **与全局策略关系** | 明确是 **叠加**：先 Owner-only / 通用管道，再 Sandbox，再 Subagent / 执行批准。 | 工具若走 gRPC，则在 **远端服务内** 统一做路径与命令约束；与「LLM 侧策略」正交。 |
| **典型用途** | 只读审查 run、灰度新工具、子 agent 降权。 | 多租户工作区、防路径穿越、防 shell 注入、磁盘配额。 |
| **mw4agent 已有近似** | `mw4agent/agents/tools/policy.py` 的 `ToolPolicyConfig` + `filter_tools_by_policy`（全局 profile/allow/deny，含 channel/user 覆盖）已类似 **通用工具策略**，但 **尚未**区分「普通 run」与「带 sandbox 标志的二次策略」。 | **无** gRPC SandboxService；仅有 per-agent `workspace_dir` 与部分 RPC 上的路径校验（如 `agents.workspace_file.*`）。 |

**结论**：要在 mw4agent 里谈「沙箱」，应 **同时规划两条线**——与 OpenClaw 对齐的 **Sandbox 工具策略（策略沙箱）**，以及可选的 **执行面隔离（执行沙箱，可借鉴 Shannon）**。二者可组合：策略沙箱缩小 LLM 可见工具；执行沙箱保证「即便工具被调用，也只能动会话目录 + 安全命令」。

---

## 2. 目标架构（mw4agent 内如何拼在一起）

```text
请求（桌面 / 频道 / CLI）
  → 身份与命令授权（已有或逐步补齐）
  → 解析 tools 全局策略（profile + allow + deny + by_channel / by_user）
  → [可选] sandbox_run == true 时叠加 SandboxToolPolicy（deny 优先 + allow 白名单）
  → 构建本轮可用工具列表 → 交给 LLM
  → tool 执行
       → [阶段 A] 仍在宿主进程内、受现有 Agent 工作区约束
       → [阶段 B] 文件/exec 类工具改为经「会话沙箱后端」执行（目录隔离 + SafeCommand 语义）
  → [可选] 高危工具二次确认（对齐 OpenClaw 的「执行批准」思路，可与桌面/Gateway UI 联动）
```

---

## 3. 分阶段实施计划

### 阶段 0：约定术语与配置形状（1～2 天）

- 在配置与文档中固定命名，避免与 `tools.profile` 混淆：
  - **`tools.sandbox`** 或顶层 **`sandbox`**：是否启用「策略沙箱」及子策略。
  - 建议字段（与 OpenClaw 语义对齐，便于迁移心智）：
    - `enabled: bool`
    - `allow: string[]`（可选；空表示仅受 deny 约束的黑名单模式）
    - `deny: string[]`（可选；**优先级最高**）
  - 与 **run 级** 开关：编排单次任务 / `agent.run` 请求体中增加 `sandbox: true`，用于桌面「仅本次沙箱运行」。
- 产出：更新 `docs/manuals/configuration.md` 或 `mw4agent/docs/` 内简短说明 + 示例 JSON。

### 阶段 1：策略沙箱（对齐 OpenClaw，纯 Python，低风险）

**目标**：不引入新进程，仅在 **工具注册/过滤** 路径上叠加一层与 `SandboxToolPolicy` 等价的逻辑。

1. **数据模型**  
   - 扩展 `ToolPolicyConfig` 或新增 `SandboxToolPolicy`（仅 sandbox 段），实现 `is_tool_allowed(policy, name)`：**deny 优先**，再处理 **allow 空 = 黑名单模式 / allow 非空 = 白名单模式**（见 `docs/openclaw/sandbox.md` 中的规则摘要）。

2. **接入点**  
   - 在构建 `AgentTool` 列表并交给 runner 之前（与 `filter_tools_by_policy` 同一调用链），若 `sandbox.enabled` 或本次 run 带 `sandbox: true`，则对工具列表 **再过滤一次**（或合并为单次 `filter_tools_with_sandbox(...)`，避免重复遍历）。

3. **执行前保底（可选但推荐）**  
   - 在实际执行 tool 前再调用一次 `is_tool_allowed`，防止配置热更新或代码路径遗漏导致的越权。

4. **观测与调试**  
   - `tools.config` 或日志中输出「本轮是否 sandbox、最终允许工具名列表摘要」，便于排障。

**验收**：配置 `deny: ["write","memory_write"]` + sandbox 开启后，模型无法通过任何路径调用被禁工具；关闭 sandbox 后行为与现网一致。

### 阶段 2：执行沙箱 — 会话工作区契约（借鉴 Shannon，仍可不引入 Rust）

**目标**：在策略沙箱之上，让 **文件读写、执行类工具** 的 **根目录** 与 **会话** 绑定，并定义生命周期。

1. **会话级目录**  
   - 在 Gateway 配置中增加例如 `sandbox_workspace_root`（默认可在 data 目录下 `sessions/`），按 **`orchestrate_session_id` 或 `agent_run_id`** 建子目录（校验 ID 字符集，防 `..`，逻辑可参考 Shannon `WorkspaceManager::validate_session_id`）。

2. **工具实现改造**  
   - `read` / `write` / 相关工具：当处于「执行沙箱模式」时，路径解析相对于 **会话目录** 而非整个 agent workspace（或采用「agent workspace 下再挂 session 子目录」的二级结构，需一次产品决策）。

3. **命令类工具**  
   - 若 mw4agent 有 `exec`/`run_terminal_cmd` 等：  
     - **最小方案**：严格 allowlist + 无 shell + 超时 + cwd=会话目录（对齐 Shannon `SafeCommand` 思路，可用 Python 子进程实现）。  
     - **较重方案**：引入对 Shannon `SandboxService` 的 gRPC 客户端（复用 Shannon 部署），mw4agent 只做会话 ID 与策略编排。

4. **回收**  
   - 在 `orchestrator` 任务结束或会话关闭 RPC 中调用 **删除会话目录**（或异步清理任务），避免磁盘堆积（Shannon 亦依赖上层调用 `delete_workspace`）。

**验收**：两路不同会话同时写文件互不覆盖；路径穿越请求被拒绝；任务结束后目录可按策略清理。

### 阶段 3：审批与多 Agent 编排协同（对齐 OpenClaw 深度）

**目标**：高危操作即使通过沙箱策略，仍需人工确认。

1. 定义 **DANGEROUS_TOOLS**（或按工具名 glob），在 Gateway 执行前后插入「待批准」状态，由桌面/Web 展示并回传批准结果（可参考 `docs/openclaw/sandbox.md` 5.2 节模式）。

2. 与 **编排**（`orchestrator`）结合：监督策略或 DAG 节点可标记 `sandbox: true` 或 `require_approval_for: ["exec"]`。

**验收**：沙箱 run 下执行危险工具会阻塞直至用户批准或超时拒绝。

### 阶段 4（可选）：与 Shannon 进程级集成

若团队已部署 Shannon agent-core：

- mw4agent 作为「编排与产品网关」，将 **session_id / user_id** 与 Shannon **对齐**，文件工具通过 `SandboxClient` 走 gRPC；**策略沙箱仍在 mw4agent 侧完成**，避免重复实现路径逻辑。

---

## 4. 建议优先级与依赖

| 优先级 | 内容 | 依赖 |
|--------|------|------|
| P0 | 阶段 1 策略沙箱 | 无 |
| P1 | 阶段 0 配置与 run 级开关 | 阶段 1 |
| P2 | 阶段 2 会话目录 + 工具路径 + 回收 | 阶段 1；需定 session ID 来源 |
| P3 | 阶段 3 审批 | 桌面/Web RPC 能力 |
| P4 | 阶段 4 Shannon gRPC | 运维与网络 |

---

## 5. 参考文档

- OpenClaw 工具策略与流程：`docs/openclaw/sandbox.md`，`docs/openclaw/auth.md`
- Shannon 执行与目录隔离：`docs/security/sandbox.md`
- mw4agent 当前工具策略：`mw4agent/agents/tools/policy.py`
