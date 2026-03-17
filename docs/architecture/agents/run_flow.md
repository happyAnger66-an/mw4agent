# MW4Agent `AgentRunner` 执行流程（run flow）

本文档描述当前 `mw4agent/agents/runner/runner.py` 中 `AgentRunner` 的实际执行流程，用于帮助理解：一轮 agent 调用如何串行化、如何加载/修复 session 历史、何时触发自动 compaction、以及工具调用循环如何运行与落盘。

---

## 1. 入口与并发模型

`AgentRunner.run(params)` 是“一轮 agent 执行”的统一入口。其并发/串行化策略是：

- **Session 维度串行**：基于 `session_key` 通过 `CommandQueue.enqueue(...)` 串行化同一会话的请求，避免并发写 transcript 导致历史错乱。
- **事件流**：在 run 的开始/结束/异常时，通过 `EventStream.emit(...)` 发送 `lifecycle` 事件；在执行工具时发送 `tool` start/end/error 事件；在开始处理时发送一条轻量的 `assistant delta`（`Processing...`）。

---

## 2. `run()` 高层流程

核心步骤如下：

- **生成标识**：`run_id`、`session_id`、`session_key`（默认会拼 `agent_id`）。
- **Session 获取/创建**：`SessionManager.get_or_create_session(...)`。
- **lifecycle start**：发出开始事件。
- **入队执行**：将 `_execute_agent_turn(...)` 作为任务投递到 `CommandQueue`（按 session 串行）。
- **lifecycle end / error**：成功则发出结束事件并返回；失败则发出 error 事件并返回一个 `is_error=True` 的 payload。

---

## 3. `_execute_agent_turn()` 主分支

该函数是“一轮 turn”核心逻辑，主要由以下模块组成：

### 3.1 技能快照（Skills Snapshot）注入

- 调用 `build_skill_snapshot()` 得到 `skills_prompt`。
- 若存在 `skills_prompt`，则把用户输入拼成：`skills_prompt + "\n\n[User]\n" + base_message` 作为本轮实际传给 LLM 的 message。
- 该快照也会 best-effort 写入 `session_entry.metadata["skills_snapshot"]`（不影响主流程）。

### 3.2 分支 A：最小工具协议（direct tool_call JSON）

如果 `params.message` 是 JSON，且形如：

```json
{
  "type": "tool_call",
  "tool_name": "gateway_ls",
  "tool_args": {"path": "."},
  "final_user_message": "请根据文件列表给出下一步建议"
}
```

则走“直连工具”路径：

- **解析工具请求**：得到 `tool_name/tool_args/final_user_message/tool_call_id`。
- **加载 tools policy**：
  - `resolve_tool_policy_config(cfg_mgr)` 得到基础策略
  - `resolve_effective_policy_for_context(...)` 得到结合 `channel/user/owner/command_authorized` 的有效策略
  - `resolve_tool_fs_policy_config(cfg_mgr)` 得到文件系统策略（如 `workspace_only`）
- **构造 `tool_context`**：包含 `workspace_dir/tools_profile/allow/deny/tools_fs_workspace_only` 等。
- **执行工具**：`execute_tool(...)`。
- **把工具输出拼入 prompt**：`final_user_message + [Tool output]`，再调用 `generate_reply(...)` 得到最终文本回复。

该路径的特点是：**只执行一次工具**，不进入标准 “LLM ↔ tools 循环”。

### 3.3 分支 B：标准对话路径（含 tools loop）

当不满足最小工具协议时，进入标准路径。主要步骤如下：

#### 3.3.1 Session transcript 历史加载（leaf-based）

- 确定 transcript 路径：`resolve_session_transcript_path(agent_id, session_id)`。
- 按 **leaf/parentId 链** 重建历史：`build_messages_from_leaf(transcript_file)`。
- **孤儿 user 修复（crash/interruption）**：
  - 若 leaf 指向的是 `role=user` 且存在 `parent_id`，则 `branch_to_parent(transcript_file, parent_id)` 回退 leaf，再重建历史。
  - 之后再做两次 `drop_trailing_orphan_user(...)` 作为兜底。

#### 3.3.2 自动 compaction 触发（可配置）

在裁剪 history 前，会调用 `_auto_compact_if_needed(...)`：

- **触发条件**：按“user turn 数”判断是否达到 `triggerTurns`。
- **压缩策略**：保留最近 `keepTurns` 个 user turn；更早的内容被折叠为一条 `type=compaction` 的 system summary。
- **落盘策略（leaf 链重写）**：
  - `branch_to_parent(..., parent_id=None)` 重置 leaf（开启新分支）
  - `append_compaction(...)` 写入 compaction 并设置 leaf
  - `append_messages(keep tail)` 重新 append 保留尾部，形成新的 leaf 链：`[compaction] -> tail`

配置位于 root config 的 `session.compaction`：

- `enabled`: `true/false`（未配置则默认不启用）
- `keepTurns`（默认 12）
- `triggerTurns`（默认 16）
- `summaryMaxChars`（默认 4000）

#### 3.3.3 history limit（按 user turns 裁剪）

之后会读取 `resolve_history_limit_turns(cfg, session_key)` 并应用 `limit_history_user_turns(history_messages, history_limit)`，用于进一步限制注入给 LLM 的历史长度。

#### 3.3.4 工具权限过滤（tools policy）

- 基于 context（`channel/sender_id/sender_is_owner/command_authorized`）计算有效策略：`resolve_effective_policy_for_context(...)`。
- 对工具列表过滤：`filter_tools_by_policy(all_tools, effective_policy)`。
- 额外 runtime 约束：非 owner 用户看不到 `owner_only` 工具。
- 将过滤后的工具构建为 OpenAI-compatible tool schema（`tool.to_dict()`）。

#### 3.3.5 进入 tools loop 或直接单次 LLM

若存在 `tool_definitions`：

- 调用 `_run_tool_loop(...)`：
  - 初始 messages：`system(extra_system_prompt)` + `history_messages` + `user`；
  - **先把 user 消息落盘**（transcript）；
  - 循环调用 `generate_reply_with_tools(...)`：
    - 无 `tool_calls`：把 assistant 文本落盘并结束；
    - 有 `tool_calls`：
      - 追加 assistant(tool_calls) 消息并落盘；
      - 逐个执行工具：每个结果以 `role=tool` 追加并落盘；
      - 进入下一轮，直到无 tool_calls 或达到 `MAX_TOOL_ROUNDS`。

若没有任何可用工具：

- 走单次 `generate_reply(...)`，messages 由 `system + history + user` 构成；
- 随后把 user + assistant 追加写入 transcript。

---

## 4. 关键数据与副作用（Side-effects）

- **transcript 落盘**：
  - tools loop：写入 user / assistant(tool_calls) / tool / assistant(final)；
  - 非 tools loop：写入 user + assistant；
  - compaction：可能会写入 `type=compaction` 并重写 leaf 链。
- **Session 统计**：
  - 本轮结束会调用 `SessionManager.update_session(...)` 更新 `message_count`。
- **usage 统计**：
  - 如果 backend 返回 `LLMUsage`，会写入 `AgentRunMeta.usage`。

---

## 5. 流程图（概览）

```mermaid
flowchart TD
  A[AgentRunner.run] --> B[SessionManager.get_or_create_session]
  B --> C[CommandQueue.enqueue by session_key]
  C --> D[_execute_agent_turn]

  D --> E[skills_snapshot 注入]
  E --> F{message 是 direct tool_call JSON?}

  F -- 是 --> G[resolve tools policy + fs policy]
  G --> H[execute_tool 一次]
  H --> I[拼 tool output -> generate_reply]
  I --> Z[返回 payload/meta]

  F -- 否 --> J[resolve transcript_file]
  J --> K[build_messages_from_leaf]
  K --> L[orphan user 修复 + drop_trailing]
  L --> M[_auto_compact_if_needed 可选]
  M --> N[resolve_history_limit_turns + limit_history_user_turns]
  N --> O[resolve_effective_policy + filter tools]
  O --> P{有可用工具?}

  P -- 否 --> Q[generate_reply(system+history+user)]
  Q --> R[append transcript: user+assistant]
  R --> Z

  P -- 是 --> S[_run_tool_loop]
  S --> T[append transcript: user]
  T --> U[generate_reply_with_tools]
  U --> V{tool_calls?}
  V -- 否 --> W[append transcript: assistant(final)]
  W --> Z
  V -- 是 --> X[append transcript: assistant(tool_calls)]
  X --> Y[execute_tool(s) + append tool results]
  Y --> U
```

---

## 6. 与 OpenClaw 对齐的点（当前已实现）

- **session 串行化**：通过 `CommandQueue` 按 session_key 串行。
- **leaf/parentId 链历史重建**：`build_messages_from_leaf`，并支持 `branch_to_parent` 修复/分叉。
- **自动 compaction（可配置）**：在 runner 内部按 turn 数触发，写入 `type=compaction` 并重写 leaf 链。
- **tools policy（channel/user 维度）**：在 runner 执行前完成工具过滤，并把上下文注入到 tool execution context。

