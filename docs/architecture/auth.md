## MW4Agent 权限控制（Tools）

本文描述 mw4agent 当前对 **tools（工具）** 的权限控制模型与实现，包含：

- tool 列表如何被裁剪（profile/allow/deny + 按 channel/user 覆盖）
- owner-only 工具如何在运行时被保护
- 文件系统工具（read/write）的 workspace 路径限制（`tools.fs.workspaceOnly`）

> 范围说明：本文仅覆盖 mw4agent 的工具权限（tool availability + tool execution guards）。命令授权（command_authorized）目前仅作为上下文透传，暂未进一步收紧 tools policy。

---

## 1. 总体模型：两类“权限”

mw4agent 的工具权限可理解为两层：

1) **可用性（tool exposure）**：本次 run 暴露给 LLM 的工具有哪些  
2) **执行约束（tool runtime guard）**：即便工具可用，执行时是否还有额外限制（例如路径是否必须在 workspace 内）

对齐 OpenClaw 的设计：

- `tools.profile/allow/deny` 影响 **工具可用性**
- `tools.fs.workspaceOnly` 影响 **文件系统工具执行约束**（与 profile 无直接绑定）

---

## 2. 配置入口（~/.mw4agent/mw4agent.json）

根配置文件位于 `~/.mw4agent/mw4agent.json`，tools 相关结构示例：

```json
{
  "tools": {
    "profile": "coding",
    "allow": ["memory_*"],
    "deny": ["write"],
    "fs": {
      "workspaceOnly": false
    },
    "by_channel": {
      "feishu": { "profile": "full" }
    },
    "by_user": {
      "owner:*": { "profile": "full" },
      "user:*": { "profile": "coding", "deny": ["exec", "write"] }
    },
    "by_channel_user": {
      "feishu:ou_xxx": { "profile": "coding", "allow": ["read"] }
    }
  }
}
```

配置读取实现：

- tools policy：`mw4agent/agents/tools/policy.py`
- tools fs policy：`mw4agent/agents/tools/fs_policy.py`
- 默认 config manager：`mw4agent/config/manager.py` / `mw4agent/config/root.py`

---

## 3. 工具可用性：profile + allow + deny

### 3.1 profiles

在 `mw4agent/agents/tools/policy.py` 中，profile 决定一份“基础 allowlist”：

- `minimal`：空（默认不暴露工具）
- `coding`：`read`、`write`、`memory_*`（当前内置的基础集合）
- `full`：`["*"]`（不过滤；允许所有非 deny 的工具）

### 3.2 allow/deny 规则

匹配规则：

- `deny` 优先级最高：一旦命中即移除
- `allow` 支持工具名或 glob（`fnmatch`），可写 `*`
- `profile` 的 allowlist 与 `tools.allow` 合并去重作为 effective allowlist

过滤函数：`filter_tools_by_policy(tools, policy)`（`mw4agent/agents/tools/policy.py`）

---

## 4. 作用域覆盖（by_channel / by_user / by_channel_user）

mw4agent 支持根据上下文覆盖 tools policy（实现：`resolve_effective_policy_for_context`）：

优先级（高 → 低）：

1) `tools.by_channel_user["<channel>:<user_id>"]`
2) `tools.by_user["owner:<user_id>"/"user:<user_id>"]`，以及 `owner:*` / `user:*`
3) `tools.by_channel["<channel>"]`
4) 全局 `tools`（profile/allow/deny）

> 这些覆盖当前仅对 tools policy 生效（工具可用性）。`tools.fs.workspaceOnly` 目前为全局项（`tools.fs`）。

---

## 5. 运行时上下文与执行入口

工具执行由 `mw4agent/agents/runner/runner.py` 负责：

- 计算 effective tools policy（含 scope 覆盖）
- 按 policy 过滤工具列表（决定暴露给 LLM 的工具集合）
- 构造 `tool_context` 并传入每次 tool 调用

当前 `tool_context` 中与权限相关的关键字段（简化）：

- `workspace_dir`
- `channel` / `sender_id` / `sender_is_owner` / `command_authorized`
- `tools_profile` / `tools_allow` / `tools_deny`
- `tools_fs_workspace_only`

---

## 6. Owner-only 工具

工具基类 `AgentTool` 支持 `owner_only` 标记（`mw4agent/agents/tools/base.py`）。  
在 runner 中，非 owner 的调用者会在运行时被过滤掉 `owner_only` 工具（避免暴露给 LLM）。

> 注：mw4agent 目前的 owner-only 保护主要发生在“工具可用性”阶段（过滤工具列表）。

---

## 7. 文件系统工具路径限制：tools.fs.workspaceOnly

### 7.1 配置项

- `tools.fs.workspaceOnly: boolean`
  - `false`（默认）：read/write 不限制在 workspace 内（对齐 OpenClaw 默认行为）
  - `true`：read/write 仅允许访问 `workspace_dir` 下的路径，否则报错

解析实现：`mw4agent/agents/tools/fs_policy.py`

### 7.2 生效位置

`tools_fs_workspace_only` 会由 runner 放进 `tool_context`，并被工具实现读取：

- `mw4agent/agents/tools/read_tool.py`
- `mw4agent/agents/tools/write_tool.py`

当 `tools_fs_workspace_only=True` 时，工具会执行 workspace root 校验并在越界时返回类似错误：

- `read: path is outside workspace root: <workspace_dir>`
- `write: path is outside workspace root: <workspace_dir>`

---

## 8. CLI 配置（工具权限）

配置命令（实现：`mw4agent/cli/configuration.py`）：

- 查看：`mw4agent configuration auth show`
- 交互式向导：`mw4agent configuration auth wizard`
- 非交互设置：
  - 设置 policy：`mw4agent configuration auth set --scope ... --profile ... --allow ... --deny ...`
  - 设置 FS 路径限制（仅全局）：  
    `mw4agent configuration auth set --scope global --fs-workspace-only`

