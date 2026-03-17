## MW4Agent 多 Agent 管理（对齐 OpenClaw）

本文说明 mw4agent 当前的多 agent 目录结构、CLI 用法，以及从旧版单文件 session store（`mw4agent.sessions.json`）迁移到 per-agent session store 的逻辑与约定。

---

## 1. 目标与原则

- **默认存在 main agent**：`main`
- **每个 agent 独立状态目录**：`agent_dir`
- **每个 agent 独立 workspace**：`workspace_dir`（供 read/write 等工具使用）
- **每个 agent 独立 session store**：`sessions/sessions.json`
- **迁移要安全/幂等**：旧 store 不删除，迁移成功后会创建备份

---

## 2. 目录结构

默认 state 目录：

- `~/.mw4agent`（可用 `MW4AGENT_STATE_DIR` 覆盖）

每个 agent 的目录结构：

```
~/.mw4agent/
  agents/
    <agentId>/
      agent.json
      workspace/
      sessions/
        sessions.json
```

关键实现位置：

- `mw4agent/config/paths.py`
- `mw4agent/agents/agent_manager.py`

---

## 3. CLI 用法

### 3.1 创建与查看 agent

- 创建：
  - `mw4agent agent create <agent_id> [--agent-dir ...] [--workspace-dir ...]`
- 列表：
  - `mw4agent agent list`
- 查看：
  - `mw4agent agent show [agent_id]`（默认 main）

实现：`mw4agent/cli/agent/register.py`

### 3.2 运行（指定 agent）

通过 Gateway RPC 运行时，传入 `agentId` 即可路由到该 agent 的 workspace 与 session store。

- `mw4agent agent run --agent-id <agentId> -m "..." ...`

Channels 运行也支持 `--agent-id`（multi-agent 模式）：

- `mw4agent channels console run --agent-id <agentId>`

---

## 4. Session store 迁移逻辑（旧 → 新）

### 4.1 背景

旧版 mw4agent 常用单文件 session store：

- `./mw4agent.sessions.json`（项目目录内）

新版本对齐为 per-agent：

- `~/.mw4agent/agents/<agentId>/sessions/sessions.json`

### 4.2 自动迁移触发点

当使用 multi-agent session manager（`MultiAgentSessionManager`）时，会在初始化阶段尝试 **best-effort 自动迁移**（仅 main）：

- 实现：`mw4agent/agents/session/migrate.py` + `mw4agent/agents/session/multi_manager.py`

### 4.3 迁移来源路径（候选）

自动迁移会尝试以下路径（按顺序）：

1) `./mw4agent.sessions.json`
2) `~/.mw4agent/mw4agent.sessions.json`（state root legacy）

### 4.4 合并策略与备份

迁移行为：

- 将旧 store 内的 sessions 合并写入目标 `sessions.json`
- 若 session_id 冲突：保留较新的 `updated_at`、更大的 `message_count/total_tokens`，metadata 合并
- 迁移成功后，会在旧文件旁创建备份：
  - `mw4agent.sessions.json.bak.<timestamp>`

### 4.5 幂等性

迁移函数会在以下情况“直接跳过”：

- legacy 文件不存在或为空
- legacy 文件中没有 session 条目

即多次启动不会重复写入造成膨胀（同 session_id 会走 merge 分支）。

---

## 5. 实现文件索引

- Agent 管理：
  - `mw4agent/agents/agent_manager.py`
  - `mw4agent/config/paths.py`
- Per-agent sessions：
  - `mw4agent/agents/session/multi_manager.py`
  - `mw4agent/agents/session/migrate.py`
- Gateway/Channels 路由到 per-agent workspace：
  - `mw4agent/gateway/server.py`
  - `mw4agent/cli/channels/register.py`

