# MW4Agent 配置项参考（`~/.mw4agent/mw4agent.json`）

本文整理 **mw4agent 当前代码实际读取/生效的全部配置项**（截至本仓库当前版本），并补充对应的环境变量覆盖与示例。

> 配置文件默认路径：`~/.mw4agent/mw4agent.json`  
> 可通过环境变量 `MW4AGENT_CONFIG_DIR` 指定配置目录（文件名仍为 `mw4agent.json`）。

---

## 1. 配置文件与优先级

### 1.1 根配置文件

- **默认路径**：`~/.mw4agent/mw4agent.json`
- **目录覆盖**：`MW4AGENT_CONFIG_DIR=<dir>` → 读取 `<dir>/mw4agent.json`
- **格式**：单个 JSON 对象，按顶层 section 组织（如 `llm`、`channels`、`tools`、`plugins` 等）

### 1.2 加密存储（ConfigManager）

mw4agent 的配置读写走 `ConfigManager` + 加密框架：

- **启用开关**：`MW4AGENT_IS_ENC=1`（默认关闭；开启后若未配置密钥会降级为明文读写并打印 warning）
- **密钥**：`MW4AGENT_SECRET_KEY`（base64 编码的 32 bytes）

> 说明：默认不启用加密；只有显式设置 `MW4AGENT_IS_ENC=1` 才会尝试加密读写。

---

## 2. `llm`：LLM Provider/Model 配置

**位置**：根配置 `llm` 段（`mw4agent/llm/backends.py`、`mw4agent/cli/configuration.py`）

### 2.1 配置项

- **`llm.provider`**：provider id（如 `echo/openai/deepseek/vllm/aliyun-bailian`）
- **`llm.model_id`**：模型 id（有些地方也兼容 `llm.model` 字段）
- **`llm.base_url`**：OpenAI-compatible base URL（可为空 → 使用 provider 默认值）
- **`llm.api_key`**：API Key（可为空 → 走 env）
- **`llm.contextWindow`**：上下文窗口大小（token，近似裁剪；可选；也兼容 `context_window`）
- **`llm.maxTokens`**：最大输出 token（传给 OpenAI-compatible 的 `max_tokens`；可选；也兼容 `max_tokens`）

### 2.2 环境变量覆盖

调用时优先级（概念化）：

- `MW4AGENT_LLM_PROVIDER` / `MW4AGENT_LLM_MODEL` / `MW4AGENT_LLM_BASE_URL`
- `llm.*`（配置文件）
- provider 默认值（若存在）

API Key 通常从以下读取（不同 provider 不同 env）：

- `OPENAI_API_KEY`（openai）
- `DEEPSEEK_API_KEY`（deepseek）
- `MW4AGENT_LLM_API_KEY`（vllm/aliyun-bailian 等通用）

可选的 runtime 限制（优先级：agent `agent.json` 的 `llm.*` → 全局 `llm.*` → env）：

- `MW4AGENT_LLM_CONTEXT_WINDOW`：等价于 `llm.contextWindow`
- `MW4AGENT_LLM_MAX_TOKENS`：等价于 `llm.maxTokens`

### 2.3 示例

```json
{
  "llm": {
    "provider": "openai",
    "model_id": "gpt-4o-mini",
    "base_url": "https://api.openai.com",
    "api_key": "sk-...",
    "contextWindow": 128000,
    "maxTokens": 4096
  }
}
```

---

## 3. `channels`：通道配置

**位置**：根配置 `channels` 段（`mw4agent/gateway/server.py`、`mw4agent/channels/plugins/feishu.py`）

### 3.1 `channels.feishu`

- **`channels.feishu.app_id`**：Feishu App ID  
- **`channels.feishu.app_secret`**：Feishu App Secret  
- **`channels.feishu.connection_mode`**：`webhook`（默认）或 `websocket`
- **`channels.feishu.mcp_user_access_token`**（可选）：飞书**用户访问令牌**，供内置插件 **`feishu-docs`** 调用官方 MCP（`fetch-doc` / `create-doc` / `update-doc`）读写云文档；与机器人 `app_secret` 不同。也可写 `user_access_token` 或 `mcp_uat`，或设置环境变量 `FEISHU_MCP_UAT` / `LARK_MCP_UAT`。
- **设备授权落盘（推荐）**：执行 `mw4agent feishu authorize` 后，令牌保存在 **`~/.mw4agent/feishu_oauth.json`**（按 `app_id` 分条，含 `refresh_token` 时可自动刷新）。插件 **`feishu-docs`** 在未设置上述环境变量/明文字段时会尝试从该文件读取（需已配置同一应用的 `app_id`/`app_secret`）。多应用可用环境变量 **`FEISHU_OAUTH_APP_ID`** 指定使用哪一条落盘记录。

环境变量优先于配置文件：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

WebSocket 模式还会用到：

- `FEISHU_ENCRYPT_KEY`（可为空）
- `FEISHU_VERIFICATION_TOKEN`

### 3.2 `channels.console`

Console channel 为内置通道，通常无需配置；可留空 `{}` 或不写。

### 3.3 示例

```json
{
  "channels": {
    "feishu": {
      "app_id": "cli_xxx",
      "app_secret": "xxx",
      "connection_mode": "webhook"
    },
    "console": {}
  }
}
```

---

## 3.5 `skills`：技能目录、全局 filter 与智能体白名单

**位置**：根配置 `skills` 段（`mw4agent/agents/skills/snapshot.py`）；每智能体覆盖在 `~/.mw4agent/agents/<agentId>/agent.json` 的 `skills` 字段（`mw4agent/agents/agent_manager.py`）。

### 3.5.1 全局 `skills`（根配置）

- **`skills.filter`**：字符串数组，**技能名白名单**（仅这些名称会进入模型侧技能目录/提示）。留空或不写表示不做全局名称过滤。
- **`skills.limits`**（可选）：
  - **`maxSkillsInPrompt`**（或 `max_skills_in_prompt`）：注入提示的最大技能条数。
  - **`maxSkillsPromptChars`**（或 `max_skills_prompt_chars`）：技能提示块最大字符数。
- **`skills.load`**（可选）：额外扫描目录等（见代码 `load.paths` / `extra_dirs`）。

> **方案 B（交集）**：若同时配置了全局 `skills.filter` 与某智能体 `agent.json` 的 `skills`，运行时有效集合为 **二者交集**。仅配置其一则只使用该层。

### 3.5.2 每智能体 `skills`（`~/.mw4agent/agents/<agentId>/agent.json`）

- **`skills`**：字符串数组，表示该智能体允许的技能名；与全局 `skills.filter` **取交集**。
- **`skills: []`**：显式空列表表示该智能体**不注入任何技能**到提示。
- **省略 `skills` 字段**：不施加每智能体覆盖，仅受全局 `skills.filter`（若有）约束。

桌面端可在 **配置 → skills** 中编辑全局段并保存根配置，并对指定智能体保存/清除 `skills`（RPC：`agents.update_skills`）。

示例（根配置片段）：

```json
{
  "skills": {
    "filter": ["notes", "research"],
    "limits": {
      "maxSkillsInPrompt": 150,
      "maxSkillsPromptChars": 30000
    }
  }
}
```

示例（每智能体 `agent.json` 片段）：

```json
{
  "skills": ["notes"]
}
```

---

## 4. `tools`：工具开关、权限策略、FS 策略、Web 工具配置

**位置**：根配置 `tools` 段（`mw4agent/agents/tools/policy.py`、`mw4agent/agents/tools/fs_policy.py`、`mw4agent/agents/tools/web_search_tool.py`、`mw4agent/cli/configuration.py`）

### 4.1 全局 tools policy：`tools.profile / tools.allow / tools.deny`

- **`tools.profile`**：`minimal` / `coding` / `full`
  - `minimal`：无工具（LLM-only）
  - `coding`：`read/write/memory_*` 等基础工具，以及 glob **`feishu_*`**（供已加载的飞书文档等插件注册的工具通过策略过滤；未加载插件时不产生额外工具）
  - `full`：所有工具（`*`）
- **`tools.allow`**：显式允许列表（工具名或 glob）
- **`tools.deny`**：显式禁止列表（工具名或 glob），优先级最高

示例：

```json
{
  "tools": {
    "profile": "coding",
    "deny": ["write", "memory_write"]
  }
}
```

### 4.2 分层覆盖：`tools.by_channel / tools.by_user / tools.by_channel_user`

用于按 channel/user 维度覆盖全局 policy。优先级（高→低）：

1. `tools.by_channel_user["<channel>:<user_id>"]`
2. `tools.by_user["owner:<user_id>"]` / `tools.by_user["user:<user_id>"]`（并支持 `owner:*` / `user:*`）
3. `tools.by_channel["<channel>"]`

示例：

```json
{
  "tools": {
    "by_channel": {
      "feishu": { "profile": "coding", "deny": ["write"] }
    },
    "by_user": {
      "owner:local": { "profile": "full" }
    },
    "by_channel_user": {
      "feishu:ou_xxx": { "profile": "minimal" }
    }
  }
}
```

### 4.3 Sandbox 工具策略（run 级二次收口）：`tools.sandbox.*`

在常规 `tools.profile/allow/deny` 过滤完成后，mw4agent 支持在 **sandbox run** 中再叠加一层工具策略（语义对齐 OpenClaw 的 `SandboxToolPolicy`）：

- **`tools.sandbox.enabled`**：是否启用（默认 `false`）
- **`tools.sandbox.deny`**：黑名单（glob/工具名），**优先级最高**
- **`tools.sandbox.allow`**：白名单（glob/工具名）
  - `allow` 为空：黑名单模式（仅按 deny 禁止，其余允许）
  - `allow` 非空：白名单模式（必须命中 allow 且不命中 deny）

示例（只读审查沙箱）：

```json
{
  "tools": {
    "sandbox": {
      "enabled": true,
      "allow": ["read", "memory_*", "web_search"],
      "deny": ["write", "apply_patch", "exec", "process_*"]
    }
  }
}
```

run 级开关：

- Gateway 的 `agent.run` 请求参数中可传 `sandbox: true`，表示**仅本次 run** 强制启用 sandbox 过滤。

目录隔离（宿主文件系统，非 Docker）：

- **`tools.sandbox.directoryIsolation`**：是否把 `read` / `write` / `exec` / `process_*` 的根目录切到**会话专属目录**（默认：未设置时，只要本轮 sandbox 生效就 **开启**；设为 `false` 可只做工具策略、不改变目录）
- **`tools.sandbox.workspaceRoot`**：会话沙箱根路径（默认：`~/.mw4agent/sandbox-sessions`，可用环境变量 `MW4AGENT_SANDBOX_WORKSPACE_DIR` 覆盖）
- 布局：`<workspaceRoot>/<agentId>/<sessionId>/`。长期记忆 `memory_*` 仍绑定**智能体 workspace**（`agent_workspace_dir`），不会写到会话沙箱目录。

执行隔离（预留，当前不生效）：

- **`tools.sandbox.executionIsolation`**：`none`（默认）或 `wasm`。设为 `wasm` 时仅记录意图：WASM 执行后端**尚未实现**，工具仍在宿主上运行，仅受目录隔离与策略约束。

### 4.4 FS 策略：`tools.fs.workspaceOnly`

控制文件系统类工具是否被限制在 workspace 下：

- **`tools.fs.workspaceOnly`**：`true/false`（默认 `false`）

### 4.5 Web Search：`tools.web.search.*`

`web_search` 工具的配置位于：

- **`tools.web.search.enabled`**：是否启用并暴露给模型（当前默认 **false**，需要显式开启）
- **`tools.web.search.provider`**：`brave`、`perplexity` 或 `serper`（可选；不填则按 key 自动选择：perplexity → brave → serper）
- **`tools.web.search.apiKey`**：通用 key（可选；也兼容写成 `api_key`）
- **`tools.web.search.brave.apiKey`**：Brave key（可选）
- **`tools.web.search.perplexity.apiKey`**：Perplexity key（可选）
- **`tools.web.search.serper.apiKey`**：Serper（google.serper.dev）key（可选；请求头 `X-API-KEY`）
- **`tools.web.search.proxy`**：可选 HTTP(S) 代理 URL（如 `http://127.0.0.1:7890`）；也可写在 `tools.web.search.<provider>.proxy` 覆盖
- **`tools.web.search.timeoutSeconds`**：请求超时（默认 10s）
- **`tools.web.search.cacheTtlMinutes`**：缓存 TTL（默认 5min）
- **`tools.web.search.maxResults`**：默认结果数（默认 5，上限 10）

API Key 解析优先级（高→低）：

1. `tools.web.search.<provider>.apiKey`（或 `api_key`）
2. `tools.web.search.apiKey`（或 `api_key`）
3. 环境变量（见下）

环境变量（provider key）：

- `BRAVE_API_KEY`
- `PERPLEXITY_API_KEY`
- `SERPER_API_KEY`

代理（可选，与 `tools.web.search.proxy` 等价之一即可）：`MW4AGENT_WEB_SEARCH_HTTPS_PROXY`、`HTTPS_PROXY`、`HTTP_PROXY`

示例：

```json
{
  "tools": {
    "web": {
      "search": {
        "enabled": true,
        "provider": "perplexity",
        "perplexity": { "apiKey": "pplx-..." }
      }
    }
  }
}
```

Serper 示例：

```json
{
  "tools": {
    "web": {
      "search": {
        "enabled": true,
        "provider": "serper",
        "proxy": "http://127.0.0.1:7890",
        "serper": { "apiKey": "YOUR_SERPER_KEY" }
      }
    }
  }
}
```

---

## 5. `plugins`：插件加载

**位置**：根配置 `plugins` 段（`mw4agent/plugin/loader.py`）

### 5.1 配置项

- **`plugins.plugin_dirs`**：插件目录列表（每个目录可以是 plugin root 或包含多个 plugin root 的父目录）
- **`plugins.plugins_enabled`**：允许加载的插件名列表（`null/缺省` 表示不限制）

### 5.2 环境变量覆盖

- `MW4AGENT_PLUGIN_DIR`：插件目录（支持 `:` 或 `,` 分隔），优先于 `plugins.plugin_dirs`
- `MW4AGENT_PLUGIN_ROOT`：保留字段（当前实现中定义但不一定被使用；建议以 `MW4AGENT_PLUGIN_DIR` 为准）

示例：

```json
{
  "plugins": {
    "plugin_dirs": ["~/mw4agent-plugins"],
    "plugins_enabled": ["feishu-openclaw-plugin"]
  }
}
```

---

## 6. `mw4agent.session`：Session 历史注入与 compaction（Runner 侧）

**位置**：根配置顶层 `mw4agent` 段内的 `session` 子段  
（读取点：`mw4agent/agents/runner/runner.py` 使用 `cfg_mgr.read_config("mw4agent")`）

### 6.1 历史注入裁剪

- **`mw4agent.session.historyLimitTurns`**（或 `history_limit_turns`）：最多保留多少个 user turn 的历史（注入到 LLM 前裁剪）
- **环境变量覆盖**：`MW4AGENT_HISTORY_LIMIT_TURNS=<int>`

### 6.2 自动 compaction

`mw4agent.session.compaction`：

- **`enabled`**：是否启用自动 compaction
- **`keepTurns`**：保留最近 N 个 user turns（默认 12）
- **`triggerTurns`**：达到多少 user turns 触发 compaction（默认 16）
- **`summaryMaxChars`**：自动摘要最大字符数（默认 4000）

示例：

```json
{
  "mw4agent": {
    "session": {
      "historyLimitTurns": 12,
      "compaction": {
        "enabled": true,
        "keepTurns": 12,
        "triggerTurns": 16,
        "summaryMaxChars": 4000
      }
    }
  }
}
```

---

## 7. 运行时目录相关（环境变量，不在 `mw4agent.json`）

这些属于“运行时路径布局”，不在 root config 中配置：

- **`MW4AGENT_STATE_DIR`**：状态目录（默认 `~/.mw4agent`）
- **`MW4AGENT_WORKSPACE_DIR`**：workspace 目录全局覆盖（默认 `~/.mw4agent/agents/<agentId>/workspace`，不建议轻易覆盖多 agent 情况）

---

## 8. Gateway/Channels 相关环境变量（不在 `mw4agent.json`）

- **`MW4AGENT_GATEWAY_URL`**：channels CLI 通过 Gateway RPC 调用 agent 时使用的 base URL
- **`GATEWAY_NODE_TOKEN`**：Gateway node 连接鉴权 token（也可通过 `mw4agent gateway run --node-token` 传入）

---

## 9. 日志配置（环境变量）

**位置**：`mw4agent/log/__init__.py`

- `MW4AGENT_LOG_LEVEL`：`DEBUG|INFO|WARNING|ERROR`（默认 INFO）
- `MW4AGENT_LOG_CONSOLE`：`1|0|true|false`（默认 1）
- `MW4AGENT_LOG_FILE`：日志文件路径（开启文件轮转）
- `MW4AGENT_LOG_FILE_MAX_BYTES`：单文件最大字节（默认 10485760）
- `MW4AGENT_LOG_FILE_BACKUP_COUNT`：备份数量（默认 5）
- `MW4AGENT_LOG_HOST`：`host:port` TCP log host
- `MW4AGENT_LOG_FORMAT`：自定义 format

