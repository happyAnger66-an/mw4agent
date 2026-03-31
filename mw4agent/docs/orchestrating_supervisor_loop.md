# 监督式流水线迭代编排（Supervisor + Router）设计方案

本文档描述在 **mw4agent Gateway `Orchestrator`** 上新增一种编排策略：在固定顺序的多 Agent 流水线（例如 **A → B → C**）每一轮末尾，由 **顶层监督 LLM** 根据当前产物（尤其 **C 本轮输出**）决定 **结束** 或 **再开一轮宏迭代**（反馈回到 A）。  

该模型对应 Shannon 思路里的 **Supervisor / Router** 分层：底层是「谁按什么顺序说话/干活」，顶层是「任务是否达成、要不要再来一遍」。

**定位**：与现有 `dag`（**无环图、单次执行**）、`router_llm`（**每轮仅选下一个说话人**）、`round_robin`（**轮次轮换**）互补；**不在 DAG 边上画环**，用 **显式外循环 + 监督门控** 实现 A→B→C→…→A 的「反馈迭代」。

---

## 1. 目标与非目标

### 1.1 目标

- **固定流水线顺序**：配置 `pipeline: [agentA, agentB, agentC]`（均为已存在 `agentId`，且建议 ⊆ `participants`）。
- **宏迭代（macro-iteration）**：每一轮按顺序执行完整个流水线（A→B→C），得到本轮最终产出（默认取 **C 的输出** 作为「本轮交付物」）。
- **监督决策**：每轮结束后调用 **监督 LLM**（独立 `supervisorLlm` 配置，字段可与现有 `routerLlm` 对齐：`provider` / `model` / `base_url` / `api_key` / `thinking_level`）。
  - 输出结构化结论：**继续** 或 **停止**，并可选 **下一轮给 A 的简要任务说明/修正指令**。
- **终止条件**（组合满足其一即停）：
  - 监督判定 `stop`；
  - 达到 **`supervisorMaxIterations`**（硬上限）；
  - 单次 `send` 触发的后台任务异常 → 现有 `status=error` 行为。
- **可观测**：在 `messages` 或扩展字段中记录 **监督决策摘要**（便于桌面端展示「为何继续/结束」）。

### 1.2 非目标（首版可不做的）

- 不在监督里自动 **改写 DAG** 或 **动态增删流水线节点**（后续可演进）。
- 不把监督决策做成 **HITL 人工审批**（可与未来 `orchestrate.signal` 类 API 扩展）。
- 不承诺与 Shannon Temporal 的 **可回放** 语义一致；仍以当前 **进程内 asyncio 任务 + orch.json** 为真源。

---

## 2. 与现有策略的对比

| 维度 | `round_robin` | `router_llm` | `dag` | **本方案 `supervisor_pipeline`** |
|------|----------------|--------------|-------|-----------------------------------|
| 单轮内顺序 | 每次一人 | 每次一人（LLM 选人） | 拓扑层级/并行 | **固定全序 A→B→C** |
| 能否「回到 A」 | 多轮 user send 或 maxRounds | 同上 | **不能**（无环单次） | **能**（监督批准后的宏迭代） |
| 顶层 LLM | 无 | **每轮选人** | 无 | **每轮流水线结束再判续停** |
| 典型场景 | 群聊轮流 | 动态角色 | 任务依赖、并行 | **质检/迭代完善闭环** |

---

## 3. 概念与术语

- **节拍（stroke）**：单次 `A.run → B.run → C.run` 串联完成，记为一轮宏迭代。
- **监督门（supervisory gate）**：在节拍末尾、调用监督 LLM 的一次决策。
- **工作备忘录（working brief）**：监督在 `continue` 时可生成短文本，作为 **下一轮 A 的 user 侧上下文补充**（与用户原始需求一起注入 prompt），避免无限重复同一字面指令。

---

## 4. 配置模型（建议）

### 4.1 `OrchState` / `orch.json` 扩展字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `strategy` | string | 新增取值：`supervisor_pipeline` |
| `supervisorPipeline` | string[] | 流水线 agentId 顺序，如 `["a1","b1","c1"]` |
| `supervisorLlm` | object? | 与 `routerLlm` 同形；**独立于** `routerLlm` |
| `supervisorMaxIterations` | int | 单次 `send` 允许的宏迭代上限，默认如 `5` |
| `supervisorLlmMaxRetries` | int | 监督 LLM **单次调用**失败或空响应后的最大**重试次数**（不含首次）；每次重试前等待 **10s**；默认 `12`，范围 0–64 |
| `supervisorIteration` | int | 当前已完成节拍计数（持久化便于断点排查；首版可从 0 递推） |
| `supervisorLastDecision` | object? | 可选：`{ "action": "continue"|"stop", "reason": "...", "brief": "..." }` |

`participants` 仍建议 **等于** `pipeline` 涉及的 agent 并集（或为其超集），以便于桌面端展示与权限一致；实现上 **以 `supervisorPipeline` 为执行顺序真源**。

### 4.2 RPC / 创建编排

- `orchestrate.create` / `update`：允许传入 `strategy: "supervisor_pipeline"`、`supervisorPipeline`、`supervisorLlm`、`supervisorMaxIterations`、`supervisorLlmMaxRetries`（或 snake_case RPC 别名）。
- `orchestrate.get`：返回上述字段（`supervisorLlm` 对外可同 `routerLlm` 一样遮蔽 `api_key`）。

---

## 5. 执行流程（算法）

以下描述单次用户消息触发 `send` 后的后台任务 `_task_supervisor_pipeline`（名称待定）。

**输入状态**：`st.messages` 已追加本轮 **user** 消息（与现网一致）。

1. 令 `macro = 0`，`original_user_text =` 本轮触发的用户消息正文（若需与历史区分，可用「最后一条 role=user」）。
2. **While** `macro < supervisorMaxIterations` **且** `st.status == running`：
   1. 令 `stroke_input_for_A =` 拼接上下文：
      - 首宏迭代：`original_user_text`；
      - 否则：`original_user_text` + 监督上轮给出的 `brief`（若有）+ 可选「上一拍 C 输出摘要」占位说明。
   2. **For** `agent_id` **in** `supervisorPipeline`（按序）：
      - 解析 `session_id`、`workspace_dir`、`extra_system_prompt`（沿用编排 hints），调用 `runner.run(AgentRunParams(...))`。
      - 将 assistant 消息追加到 `st.messages`（`speaker=agent_id`，`round` 建议用「全局递增 round」或与现有 `currentRound` 规则对齐；需定义 **同一宏迭代内多条 assistant 的 round/node 标记**，避免与 DAG `nodeId` 混淆，可用 `meta` 字段或约定 `round = macro * 100 + step` 仅作显示）。
      - 链式传递：`last_text =` 该 agent 输出，作为下一 agent 的 `message`（与现网 linear 类似）。
   3. 令 `c_output = last_text`（或显式配置「闭环节点」为流水线最后一格，以支持未来流水线变长）。
   4. **监督门**：构造 prompt（见 §6），调用 `_call_openai_chat`（`agent_id` 日志可记 `"{orchId}:supervisor"`），解析响应。
   5. 若 `action == stop`：写 `supervisorLastDecision`，`st.status = idle`，`break`。
   6. 若 `action == continue`：写 `supervisorLastDecision`，`macro += 1`，**保留** `brief` 供下一轮 A；**禁止**在无 `brief` 时死循环（若模型未给 brief，可用默认文案如「根据上一轮输出修正并推进任务」并打点日志）。
3. 若循环因 **达到 `supervisorMaxIterations`** 结束且仍为 `running`：写一条系统可见的 assistant 或 `messages` 内说明「已达最大迭代次数」，`st.status = idle`（或 `error` 由产品定；建议 **idle + 末尾说明** 更友好）。
4. `_save(st)` 各关键点与现网一致。

**并发**：单次 `send` 仅跑一个后台任务；与其它 strategy 一样 `running` 时拒绝第二次 `send`。

---

## 6. 监督 LLM 契约（建议 JSON）

要求模型 **只输出一段 JSON**（便于解析；失败时走 §8 回退）。

```json
{
  "action": "continue",
  "reason": "C 的输出未覆盖用户要求的验收项 X，需要 A 补充检索。",
  "brief_for_next_stroke": "请针对验收项 X 补充证据并压缩结论，再交给后续角色审核。"
}
```

或：

```json
{
  "action": "stop",
  "reason": "已满足用户目标，C 的总结可作为最终答案。",
  "final_user_visible_summary": "可选：写给用户的简短结语"
}
```

字段约定：

| 字段 | 必填 | 说明 |
|------|------|------|
| `action` | 是 | `continue` \| `stop` |
| `reason` | 建议 | 日志与 UI |
| `brief_for_next_stroke` | `continue` 时强烈建议 | 下一轮注入 A 的指令补丁 |
| `final_user_visible_summary` | 可选 | 停止时追加一条「编排总结」消息（speaker 可为 `supervisor` 或固定别名） |

**Prompt 应包含**（摘要列表）：

- 用户原始目标（`original_user_text`）；
- 本轮流水线各角色输出 **截断摘要**（控制 token，例如每段 1–2k 字）；
- **`c_output` 全文或摘要**；
- `macro` / `supervisorMaxIterations`；
- 硬规则：必须输出合法 JSON；`continue` 时必须给出可执行的 `brief_for_next_stroke`。

---

## 7. 消息与 UI 建议

- **最小改动**：仍用现有 `OrchMessage`；监督决策可 **不**单独成消息，仅存 `supervisorLastDecision`（桌面「详情」里展示）。  
- **增强体验**：在 `stop` 时追加一条 `speaker: "supervisor"`、`role: "assistant"` 的短消息（需在类型上允许 `speaker` 非参与者 id，或在 `participants` 中虚拟加入 `supervisor` 仅用于展示）。
- 桌面 `OrchestratePanel`：策略为 `supervisor_pipeline` 时展示 **当前宏迭代 / 上限**、最近 **监督 reason**。

---

## 8. 鲁棒性

- **JSON 解析失败**：默认 `stop`，`st.error` 或消息中写入「监督输出无法解析」，`status=idle` 或 `error`（建议 **error** 便于用户重试配置模型）。
- **监督返回未知 `action`**：视为 `stop`。
- **`continue` 但已达 `supervisorMaxIterations`**：不进入下一轮，直接 idle + 说明。
- **Token 预算**：监督 prompt 对历史做 **滑动摘要**（未来可与单 agent compaction 对齐；首版可用硬截断）。

---

## 9. 与 Shannon 的对应关系（概念）

- **Shannon**：`Supervisor` / `Orchestrator` 路由 + 子工作流；DAG **不允许环**。  
- **本方案**：**DAG 仍用于单次无环交付**；**带环的多角色反馈**由 **`supervisor_pipeline` 的外层 while** 承担，语义上等价于「每轮子任务完成后由监督决定是否再调度一轮子流水线」，无需在图里画回边。

---

## 10. 落地阶段（建议）

| 阶段 | 内容 |
|------|------|
| P0 | `OrchState` 字段 + `create/update/get` + `_task_supervisor_pipeline` + 单元测试（解析 mock、迭代次数、stop/continue） |
| P1 | 桌面创建编排表单项：`supervisorPipeline` 排序 UI、`supervisorLlm`、`supervisorMaxIterations` |
| P2 | WS/事件：宏迭代开始/结束、监督决策（可选） |
| P3 | 监督 prompt 模板可配置（`orch.json` 或全局 config） |

---

## 11. 测试要点

- 流水线长度为 1 时退化为「每轮后监督」（与产品确认是否禁用）。
- `supervisorMaxIterations=1`：至多一轮节拍 + 一次监督。
- 全 echo provider：监督可测解析与状态机，不依赖外网。

---

## 12. 参考代码入口（当前仓库）

- 编排主逻辑：`mw4agent/gateway/orchestrator.py`（`router_llm` 每轮选人、`_task_dag` 无环执行）
- LLM 调用：`mw4agent/llm/backends.py` 中 `_call_openai_chat`
- 编排语义索引：`mw4agent/docs/orchestrating_shannon.md`（Shannon 策略分层）
- DAG 与环： `mw4agent/gateway/dag_spec.py`（**本方案不修改 DAG 环语义**）

---

*文档版本：与使用者讨论 Supervisor/Router 闭环需求后整理；实现以代码与 `orch.json` 实际字段为准。*
