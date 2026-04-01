# mw4agent：LLM Router Orchestrator（仅 `strategy=router_llm`）

本文档说明 `mw4agent/gateway/orchestrator.py` 中 **`router_llm` 策略**下：

- 如何选择下一位 agent（含回退）
- router 的 prompt 与 agent 侧的 context 注入

其它线性策略（`round_robin`）共用同一 `_task_linear()` 循环，但**不调用** router LLM；`dag` / `supervisor_pipeline` 走其它任务，不在此文档展开。

---

## 状态字段（与本策略相关）

| 字段 | 作用 |
|------|------|
| `strategy == "router_llm"` | 启用动态选 speaker |
| `participants` | 候选 agent id 列表（router 必须从中择一） |
| `routerLlm` | router 所用 OpenAI-compatible 配置（provider/model/base_url/api_key/thinking_level） |
| `routerAgentRoles` | **可选**：`agentId ->` 用户填写的角色/职责说明；**每次**调用 router 选 speaker 时写入同一条 user prompt |
| `maxRounds` | 单次用户消息触发的 assistant 轮数上限 |
| `pendingDirectAgent` / `pendingSingleTurn` | 定向首轮（见下） |
| `messages` | 编排级历史；router 会取「自上次用户消息以来」的 transcript |

---

## 选 agent：优先级与回退

每一轮（循环变量 `r`）在运行 agent 之前决定 `agent_id`：

### 1. 定向首轮（跳过 router）

若 `send(..., target_agent=...)` 指向合法 `participants`，则：

- 仅**第一轮**（`r == start_round`）强制 `agent_id = target`
- `router_llm` **不调用** router 模型
- 若 `pendingSingleTurn`：整段只跑 **1** 轮 assistant

### 2. 默认基线：round-robin

在未命中定向时，先设：

- `agent_id = participants[r % len(participants)]`

作为 **router 失败或解析失败时的稳定回退**。

### 3. Router LLM 覆盖

当 `strategy == "router_llm"` 且配置了 `routerLlm`，且**不是**定向首轮时：

1. 调用 `_call_openai_chat()`，使用 `_build_router_llm_user_prompt()` 组装的 **user prompt**（见下节）。
2. 用 `_parse_router_agent_pick()` 解析回复：
   - 优先 **JSON**：`{"next_agent":"<id>"}`（可带 \`\`\` 围栏；也可从回复中抠出第一段 `{...}`）
   - 否则取**第一行**纯文本，且必须在 `participants` 中
3. 解析成功则 `agent_id = pick`；否则保持 round-robin 基线。
4. 调用或解析过程抛错时：`logger.warning(...)`，并回退 round-robin（不再静默吞掉）。

---

## Router 的 prompt：注入了哪些 context？

Router 不再只看「上一轮字符串 `last_text`」，而是显式包含：

1. **候选列表**：`Candidates (pick exactly one id verbatim): ...`
2. **Agent identity / responsibilities**：对 `participants` 中每个 id 一行；若配置了 `routerAgentRoles[id]` 则附用户写的职责说明，否则 `(no role description)`。用于让 router 按「谁擅长什么」匹配下一步。
3. **批次内轮次**：`Assistant turn in this user-message batch: <cur> of <max>`
4. **Original user request**：当前编排中**最后一条用户消息**全文（截断至 `_ROUTER_LLM_ORIGINAL_USER_MAX_CHARS`）；若缺失则回退为当前的 `last_text`
5. **Orchestration transcript**：自**最后一条用户消息**起，到 `st.messages` 末尾的格式化片段（`[user]` / `[agentId]` 标签），截断至 `_ROUTER_LLM_TRANSCRIPT_MAX_CHARS`
6. **Immediate context**：与当前轮 agent 输入一致的主线——首轮为用户侧内容，后续为上一 agent 输出（截断 `_ROUTER_LLM_IMMEDIATE_MAX_CHARS`）

Router 被要求只输出 **单行 JSON**：`{"next_agent":"<candidate_id>"}`。

常量定义在 `orchestrator.py`：` _ROUTER_LLM_*`；单条角色说明最长 `_ROUTER_AGENT_ROLE_MAX_CHARS`。

### API / 持久化

- 创建或更新编排时传入 **`routerAgentRoles`**（JSON 对象，与 `orchestrate.create` / `orchestrate.update` / `orchestrate.run` 的 `params` 同级；也可用 snake_case `router_agent_roles`）。
- **仅 `strategy=router_llm`** 时会写入并用于路由；其它策略下不会保留该字段（置空）。
- **update**：传入 `routerAgentRoles` 会与已有合并：同一 key 覆盖；值为空字符串则删除该 agent 的角色条目；不传该字段（`null`/省略）则只按新 `participants` 过滤旧角色。
- `orchestrate.get` 的 payload 中含 **`routerAgentRoles`**（对象，无敏感字段）。

---

## Agent 侧：prompt / context 如何注入？

每次仍通过 `AgentRunParams`：

### `extra_system_prompt`

- `bootstrap = load_bootstrap_for_orchestration(cfg.workspace_dir, workspace_dir)`
- 对 `router_llm` 使用略长的 **orch_hint**，说明若 user message 里出现 `[Original user request]` / `[Previous agent output]`，应在用户目标下接续前序工作。

### `message`（本轮用户可见输入）

| 情况 | 内容 |
|------|------|
| 首轮且最后一条为 user | 对用户原文做 `_strip_at_mentions`；若删空则回退原文 |
| **`router_llm` 且 `r > start_round`** | 结构化两段：`[Original user request]` + 最后用户消息；`[Previous agent output]` + `last_text`（链式上一棒输出） |
| 其它（如 round_robin 的后续轮） | `last_text` |

这样后续轮次的 agent 能同时看到**原始用户目标**与**上一 agent 产出**，避免只收到中间片段而丢失意图。

### 会话与工作区（不变）

- `session_key = f"orch:{orch_id}"`
- `session_id` 按 `agent_id` 在 `agentSessions` 中稳定复用
- `workspace_dir` 按 `orch_id` + `agent_id` 隔离

---

## 小结

| 维度 | `router_llm` 行为 |
|------|-------------------|
| 选 agent | 定向首轮 > router JSON/首行解析成功 > round-robin |
| Router 输入 | 用户目标 + 自上次 user 起的 transcript + 立即上下文 + 轮次信息 |
| Agent 输入（第 2 轮起） | 原始用户请求块 + 上一 agent 输出块 |
| 失败 | 打日志 + round-robin |
