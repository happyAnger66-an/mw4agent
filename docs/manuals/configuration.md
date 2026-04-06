# Orbit 配置项参考（`~/.orbit/orbit.json`）

本文整理 **orbit 当前代码实际读取/生效的全部配置项**（截至本仓库当前版本），并补充对应的环境变量覆盖与示例。

> 配置文件默认路径：`~/.orbit/orbit.json`  
> 可通过环境变量 `ORBIT_CONFIG_DIR` 指定配置目录（文件名仍为 `orbit.json`）。

---

## 1. 配置文件与优先级

### 1.1 根配置文件

- **默认路径**：`~/.orbit/orbit.json`
- **目录覆盖**：`ORBIT_CONFIG_DIR=<dir>` → 读取 `<dir>/orbit.json`
- **格式**：单个 JSON 对象，按顶层 section 组织（如 `llm`、`channels`、`tools`、`plugins` 等）

**未设置 `ORBIT_CONFIG_DIR` 时，实际文件路径按以下顺序取「第一个已存在的文件」**（见 `orbit/config/root.py`）：

1. `~/.orbit/orbit.json`
2. `~/.orbit/config/orbit.json`
3. `~/orbit/orbit.json`
4. `~/orbit/config/orbit.json`
5. `~/.mw4agent/mw4agent.json`
6. `~/.mw4agent/config/mw4agent.json`

若均不存在，则默认写入目录为 `~/.orbit/`（新建 `orbit.json`）。

### 1.2 重启后配置像被清空 / 恢复默认（排查）

常见原因：

1. **多个根配置文件并存**：只要 **`~/.orbit/orbit.json` 存在**（哪怕是 `{}`），就会优先于 `~/orbit/orbit.json` 等路径。若某次启动创建了空的 `~/.orbit/orbit.json`，之后会一直读它，看起来像「只剩默认」。处理：合并内容到 `~/.orbit/orbit.json`，或删掉无用副本，或设置 `ORBIT_CONFIG_DIR` 指向唯一目录。
2. **启动环境不同**：systemd / 桌面启动与终端里 **`HOME` 不一致**，会读到不同用户目录下的配置。
3. **加密读写失败**：开启 `ORBIT_IS_ENC=1` 后若密钥与文件不匹配，读配置可能失败；**不要**在解密失败时用 UI 保存整段配置覆盖文件，以免覆盖加密内容。先恢复 `ORBIT_SECRET_KEY` 或暂时关闭加密再排查。
4. **与「状态目录」混淆**：`ORBIT_STATE_DIR`（`~/.orbit` 等）管 agent 数据；**根配置**由上面列表与 `ORBIT_CONFIG_DIR` 决定，二者不一定在同一路径树下。

启动时若检测到多个候选 `orbit.json`/`mw4agent.json`，进程会 **`warnings.warn` 一次** 说明正在使用哪一个、忽略了哪些。

调试可执行：`python -c "from orbit.config.root import get_root_config_path, list_existing_root_config_files; print(get_root_config_path()); print(list_existing_root_config_files())"`。

### 1.3 加密存储（ConfigManager）

orbit 的配置读写走 `ConfigManager` + 加密框架：

- **启用开关**：`ORBIT_IS_ENC=1`（默认关闭；开启后若未配置密钥会降级为明文读写并打印 warning）
- **密钥**：`ORBIT_SECRET_KEY`（base64 编码的 32 bytes）

> 说明：默认不启用加密；只有显式设置 `ORBIT_IS_ENC=1` 才会尝试加密读写。

---

## 2. `llm`：LLM Provider/Model 配置

**位置**：根配置 `llm` 段（`orbit/llm/backends.py`、`orbit/cli/configuration.py`）

### 2.1 配置项

- **`llm.provider`**：provider id（如 `echo/openai/deepseek/vllm/aliyun-bailian`）
- **`llm.model_id`**：模型 id（有些地方也兼容 `llm.model` 字段）
- **`llm.base_url`**：OpenAI-compatible base URL（可为空 → 使用 provider 默认值）
- **`llm.api_key`**：API Key（可为空 → 走 env）
- **`llm.contextWindow`**：上下文窗口大小（token，近似裁剪；可选；也兼容 `context_window`）
- **`llm.maxTokens`**：最大输出 token（传给 OpenAI-compatible 的 `max_tokens`；可选；也兼容 `max_tokens`）

### 2.2 环境变量覆盖

调用时优先级（概念化）：

- `ORBIT_LLM_PROVIDER` / `ORBIT_LLM_MODEL` / `ORBIT_LLM_BASE_URL`
- `llm.*`（配置文件）
- provider 默认值（若存在）

API Key 通常从以下读取（不同 provider 不同 env）：

- `OPENAI_API_KEY`（openai）
- `DEEPSEEK_API_KEY`（deepseek）
- `ORBIT_LLM_API_KEY`（vllm/aliyun-bailian 等通用）

可选的 runtime 限制（优先级：agent `agent.json` 的 `llm.*` → 全局 `llm.*` → env）：

- `ORBIT_LLM_CONTEXT_WINDOW`：等价于 `llm.contextWindow`
- `ORBIT_LLM_MAX_TOKENS`：等价于 `llm.maxTokens`

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

**位置**：根配置 `channels` 段（`orbit/gateway/server.py`、`orbit/channels/plugins/feishu.py`）

### 3.1 `channels.feishu`

- **`channels.feishu.app_id`**：Feishu App ID  
- **`channels.feishu.app_secret`**：Feishu App Secret  
- **`channels.feishu.connection_mode`**：`webhook`（默认）或 `websocket`
- **`channels.feishu.mcp_user_access_token`**（可选）：飞书**用户访问令牌**，供内置插件 **`feishu-docs`** 调用官方 MCP（`fetch-doc` / `create-doc` / `update-doc`）读写云文档；与机器人 `app_secret` 不同。也可写 `user_access_token` 或 `mcp_uat`，或设置环境变量 `FEISHU_MCP_UAT` / `LARK_MCP_UAT`。
- **设备授权落盘（推荐）**：执行 `orbit feishu authorize` 后，令牌保存在 **`~/.orbit/feishu_oauth.json`**（按 `app_id` 分条，含 `refresh_token` 时可自动刷新）。插件 **`feishu-docs`** 在未设置上述环境变量/明文字段时会尝试从该文件读取（需已配置同一应用的 `app_id`/`app_secret`）。多应用可用环境变量 **`FEISHU_OAUTH_APP_ID`** 指定使用哪一条落盘记录。

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

**位置**：根配置 `skills` 段（`orbit/agents/skills/snapshot.py`）；每智能体覆盖在 `~/.orbit/agents/<agentId>/agent.json` 的 `skills` 字段（`orbit/agents/agent_manager.py`）。

### 3.5.1 全局 `skills`（根配置）

- **`skills.filter`**：字符串数组，**技能名白名单**（仅这些名称会进入模型侧技能目录/提示）。留空或不写表示不做全局名称过滤。
- **`skills.limits`**（可选）：
  - **`maxSkillsInPrompt`**（或 `max_skills_in_prompt`）：注入提示的最大技能条数。
  - **`maxSkillsPromptChars`**（或 `max_skills_prompt_chars`）：技能提示块最大字符数。
- **`skills.load`**（可选）：额外扫描目录等（见代码 `load.paths` / `extra_dirs`）。

> **方案 B（交集）**：若同时配置了全局 `skills.filter` 与某智能体 `agent.json` 的 `skills`，运行时有效集合为 **二者交集**。仅配置其一则只使用该层。

### 3.5.2 每智能体 `skills`（`~/.orbit/agents/<agentId>/agent.json`）

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

**位置**：根配置 `tools` 段（`orbit/agents/tools/policy.py`、`orbit/agents/tools/fs_policy.py`、`orbit/agents/tools/web_search_tool.py`、`orbit/cli/configuration.py`）

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

在常规 `tools.profile/allow/deny` 过滤完成后，orbit 支持在 **sandbox run** 中再叠加一层工具策略（语义对齐 OpenClaw 的 `SandboxToolPolicy`）：

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
- **`tools.sandbox.workspaceRoot`**：会话沙箱根路径（默认：`~/.orbit/sandbox-sessions`，可用环境变量 `ORBIT_SANDBOX_WORKSPACE_DIR` 覆盖）
- 布局：`<workspaceRoot>/<agentId>/<sessionId>/`。在**非编排**的普通对话 run 下，长期记忆 `memory_*` 仍绑定**该 agent 的默认 workspace**（`agent_workspace_dir`），不会写到会话沙箱目录。  
  **Gateway 多智能体编排**（`orchestrator`）下各 agent 使用独立的编排 workspace，见下文「7.1」。

执行隔离（预留，当前不生效）：

- **`tools.sandbox.executionIsolation`**：`none`（默认）或 `wasm`。设为 `wasm` 时仅记录意图：WASM 执行后端**尚未实现**，工具仍在宿主上运行，仅受目录隔离与策略约束。

### 4.4 FS 策略：`tools.fs.workspaceOnly`

控制文件系统类工具是否被限制在 workspace 下：

- **`tools.fs.workspaceOnly`**：`true/false`（默认 `false`）

### 4.5 Web Search：`tools.web.search.*`

`web_search` 工具的配置位于：

- **`tools.web.search.enabled`**：是否启用并暴露给模型（当前默认 **false**，需要显式开启）
- **`tools.web.search.provider`**：`brave`、`perplexity`、`serper` 或 `playwright`（可选；不填则按 key 自动选择：perplexity → brave → serper；**不会**自动选 `playwright`）
- **`tools.web.search.apiKey`**：通用 key（可选；也兼容写成 `api_key`）
- **`tools.web.search.brave.apiKey`**：Brave key（可选）
- **`tools.web.search.perplexity.apiKey`**：Perplexity key（可选）
- **`tools.web.search.serper.apiKey`**：Serper（google.serper.dev）key（可选；请求头 `X-API-KEY`）
- **`tools.web.search.proxy`**：可选 HTTP(S) 代理 URL（如 `http://127.0.0.1:7890`）；也可写在 `tools.web.search.<provider>.proxy` 覆盖（**Playwright** 会将其传给浏览器 `launch(proxy=...)`，支持 `http`/`https`/`socks5`）
- **`tools.web.search.playwright.*`**（仅 `provider=playwright`）：可选细项
  - **`headless`**：是否无头（默认 `true`；也可写字符串 `"false"`）
  - **`browser`**：`chromium` / `firefox` / `webkit`（默认 `chromium`）
  - **`searchUrlTemplate`**：搜索 URL 模板，必须包含 `{query}`（默认 DuckDuckGo HTML：`https://html.duckduckgo.com/html/?q={query}`）。含 **`google.com`** 且带查询参数 `q=` 时走 **Google** 解析；含 **`bing.com`** 时走 **Bing** 解析
  - **`fallbackToBingOnGoogleFailure`**：当 **Google** SERP 解析结果为 **0 条** 时，是否再打开 **Bing** 重试（默认 **`false`**：Google 无结果则直接返回空列表，不访问 Bing）
  - **`timeoutMs`**：页面导航/等待超时毫秒数（默认取 `timeoutSeconds×1000`，且不少于 15000）
  - **`locale`**、**`userAgent`**：浏览器上下文语言与 UA（可选）
  - **`proxyUsername`** / **`proxyPassword`**：代理认证（可选；也可写在代理 URL 的 `user:pass@host` 中）

依赖：需安装 `pip install 'orbit[playwright]'` 并在部署环境执行 `playwright install <browser>`（例如 `chromium`）。
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

代理（可选，与 `tools.web.search.proxy` 等价之一即可）：`ORBIT_WEB_SEARCH_HTTPS_PROXY`、`HTTPS_PROXY`、`HTTP_PROXY`

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

Playwright 示例（走系统或本地 HTTP 代理，无需搜索 API Key）：

```json
{
  "tools": {
    "web": {
      "search": {
        "enabled": true,
        "provider": "playwright",
        "proxy": "http://127.0.0.1:7890",
        "timeoutSeconds": 45,
        "playwright": {
          "browser": "chromium",
          "headless": true,
          "proxyUsername": "",
          "proxyPassword": ""
        }
      }
    }
  }
}
```

---

## 5. `plugins`：插件加载

**位置**：根配置 `plugins` 段（`orbit/plugin/loader.py`）

### 5.1 配置项

- **`plugins.plugin_dirs`**：插件目录列表（每个目录可以是 plugin root 或包含多个 plugin root 的父目录）
- **`plugins.plugins_enabled`**：允许加载的插件名列表（`null/缺省` 表示不限制）

### 5.2 环境变量覆盖

- `ORBIT_PLUGIN_DIR`：插件目录（支持 `:` 或 `,` 分隔），优先于 `plugins.plugin_dirs`
- `ORBIT_PLUGIN_ROOT`：保留字段（当前实现中定义但不一定被使用；建议以 `ORBIT_PLUGIN_DIR` 为准）

示例：

```json
{
  "plugins": {
    "plugin_dirs": ["~/orbit-plugins"],
    "plugins_enabled": ["feishu-openclaw-plugin"]
  }
}
```

---

## 6. `orbit.session`：Session 历史注入与 compaction（Runner 侧）

**位置**：根配置顶层 `orbit` 段内的 `session` 子段  
（读取点：`orbit/agents/runner/runner.py` 使用 `cfg_mgr.read_config("orbit")`）

### 6.1 历史注入裁剪

- **`orbit.session.historyLimitTurns`**（或 `history_limit_turns`）：最多保留多少个 user turn 的历史（注入到 LLM 前裁剪）
- **环境变量覆盖**：`ORBIT_HISTORY_LIMIT_TURNS=<int>`

### 6.2 自动 compaction

`orbit.session.compaction`：

- **`enabled`**：是否启用自动 compaction
- **`keepTurns`**：保留最近 N 个 user turns（默认 12）
- **`triggerTurns`**：达到多少 user turns 触发 compaction（默认 16）
- **`summaryMaxChars`**：自动摘要最大字符数（默认 4000）

示例：

```json
{
  "orbit": {
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

## 7. 运行时目录相关（环境变量，不在 `orbit.json`）

这些属于“运行时路径布局”，不在 root config 中配置：

- **`ORBIT_STATE_DIR`**：状态目录（默认 `~/.orbit`）。未设置时按顺序选用**已存在**的目录：`~/.orbit` → `~/.mw4agent`（旧版）→ `~/orbit`；若均不存在则默认 `~/.orbit`（新建时使用该路径）。仍可用 `MW4AGENT_STATE_DIR` 显式指向任意目录。
- **`ORBIT_WORKSPACE_DIR`**：workspace 目录全局覆盖（默认 `~/.orbit/agents/<agentId>/workspace`，不建议轻易覆盖多 agent 情况）

### 7.1 Gateway 编排（orchestrator）与长期记忆路径

多智能体编排任务使用**按编排实例、按 agent 隔离**的 workspace，避免协作过程中写入的 `MEMORY.md` / `memory/*.md` 污染同一 agent 在独立对话中的目录。

| 用途 | 路径（`<STATE>` = `ORBIT_STATE_DIR`，默认 `~/.orbit`） |
|------|----------------------------------------------------------------|
| 编排状态（参与者、DAG、消息等） | `<STATE>/orchestrations/<orchId>/`（如 `orch.json`） |
| 编排内某 agent 的**运行 workspace**（文件工具根目录、`MEMORY.md` 等） | `<STATE>/orchestrations/<orchId>/agents/<agentId>/workspace` |
| 该 workspace 在启用根配置 `memory.enabled` 时的 **SQLite 索引** | `<STATE>/orchestrations/<orchId>/agents/<agentId>/memory/index.sqlite` |

**系统提示中的引导文件**：编排 run 下，身份类文件（如 `IDENTITY.md`、`USER.md`、`AGENTS.md` 等）仍从 **agent 配置的工作区**（`~/.orbit/agents/<agentId>/workspace` 或其 `agent.json` 覆盖路径）读取；**记忆类**（`MEMORY.md`、`memory.md`）仅从上述**编排 workspace** 读取，使编排内长期记忆与单 agent 会话分离。

**会话 transcript**（短期对话 JSONL）仍落在 `<STATE>/agents/<agentId>/sessions/<sessionId>.jsonl`；若开启 memory 索引中的 session 片段，索引会与当前 run 使用的 workspace 对应到同一套 SQLite，避免与仅文件检索脱节。

---

## 8. Gateway/Channels 相关环境变量（不在 `orbit.json`）

- **`ORBIT_GATEWAY_URL`**：channels CLI 通过 Gateway RPC 调用 agent 时使用的 base URL
- **`GATEWAY_NODE_TOKEN`**：Gateway node 连接鉴权 token（也可通过 `orbit gateway run --node-token` 传入）

---

## 9. 日志配置（环境变量）

**位置**：`orbit/log/__init__.py`

- `ORBIT_LOG_LEVEL`：`DEBUG|INFO|WARNING|ERROR`（默认 INFO）
- `ORBIT_LOG_CONSOLE`：`1|0|true|false`（默认 1）
- `ORBIT_LOG_FILE`：日志文件路径（开启文件轮转）
- `ORBIT_LOG_FILE_MAX_BYTES`：单文件最大字节（默认 10485760）
- `ORBIT_LOG_FILE_BACKUP_COUNT`：备份数量（默认 5）
- `ORBIT_LOG_HOST`：`host:port` TCP log host
- `ORBIT_LOG_FORMAT`：自定义 format

