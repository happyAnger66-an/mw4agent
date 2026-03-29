# MW4Agent CLI 使用手册

本文介绍 `mw4agent` 命令行工具的基本用法，重点围绕当前已经实现的几个命令组：

- `gateway`：运行 / 诊断 Gateway
- `agent`：通过 Gateway 触发一次智能体执行
- `channels`：运行各类通道（console / telegram / webhook / feishu）
- `config`：读写加密配置文件
- `configuration`：交互式 / 非交互式配置 LLM、channels、skills 等
- `tools`：查看当前所有工具及其启用状态限制

所有示例均假设你在仓库根目录运行：

```bash
cd /path/to/mw4agent
python -m mw4agent --help
```

---

## 1. 顶级结构概览

运行：

```bash
python -m mw4agent --help
```

可以看到当前可用命令：

- `gateway`：运行、探测和调用 Gateway RPC
- `agent`：通过 Gateway 触发一次 Agent 运行
- `channels`：运行 console / telegram / webhook / feishu 通道
- `config`：读写加密配置文件（`ConfigManager` 封装）
- `configuration`：配置 LLM provider/model 等（支持交互式向导）
- `tools`：查看工具列表、启用状态和限制原因

### 1.1 插件

可通过插件扩展 Agent 的**工具**与**技能**。启用方式：

- **环境变量**：`export MW4AGENT_PLUGIN_DIR=/path/to/plugin`（多个路径用 `:` 或 `,` 分隔）
- **配置文件**：在 `~/.mw4agent/mw4agent.json` 的 `plugins` 段中配置 `plugin_dirs`、可选 `plugins_enabled`

Gateway 启动时会自动加载插件并注册工具、合并技能。详见 [插件使用与配置](../architecture/plugins.md)。

---

## 2. `gateway` 命令组

### 2.1 启动 Gateway

```bash
python -m mw4agent gateway run \
  --bind 127.0.0.1 \
  --port 18790 \
  --session-file mw4agent.sessions.json
```

- **`--bind`**：监听地址（本机测试推荐 `127.0.0.1`）
- **`--port`**：HTTP 端口（默认 `18790`，与测试用例一致）
- **`--session-file`**：Gateway 的 session 存储文件路径

### 2.2 查看 Gateway 状态

```bash
python -m mw4agent gateway status --url http://127.0.0.1:18790
```

或输出 JSON：

```bash
python -m mw4agent gateway status \
  --url http://127.0.0.1:18790 \
  --json
```

### 2.3 直接调用 RPC 方法

```bash
python -m mw4agent gateway call health \
  --url http://127.0.0.1:18790 \
  --params '{}' \
  --json
```

也可以调用 `agent` / `agent.wait` 等方法（等价于测试里的 `_rpc_call`）：

```bash
python -m mw4agent gateway call agent \
  --url http://127.0.0.1:18790 \
  --params '{"message":"hi","sessionKey":"cli:test","sessionId":"cli-test","agentId":"cli","idempotencyKey":"test-1"}' \
  --json
```

### 2.4 其他辅助子命令

目前还提供了占位性质的：

- `gateway health`：简单封装 health 调用
- `gateway discover` / `gateway probe`：预留扩展位（当前返回静态结果）

---

## 3. `agent` 命令组（通过 Gateway 跑一次 Agent）

### 3.1 最小示例：跑一次 echo LLM

确保 Gateway 已在本地 `http://127.0.0.1:18790` 运行后：

```bash
python -m mw4agent agent run \
  --message "Hello from CLI" \
  --url http://127.0.0.1:18790
```

关键参数：

- **`--message`**：发送给 Agent 的用户消息（必填）
- **`--url`**：Gateway 地址（不填默认 `http://127.0.0.1:18790`）
- **`--session-key`**：会话 key，默认 `cli:default`
- **`--session-id`**：会话 id，默认 `cli-default`
- **`--timeout`**：`agent.wait` 超时时间（毫秒）
- **`--json`**：输出完整 `agent` + `agent.wait` 的 JSON 结果

### 3.2 带工具的运行：预先调用 `gateway_ls` 工具

`agent run` 支持在真正跑 LLM 前先调用一个工具（目前是 `gateway_ls`）并把结果注入 system prompt：

```bash
python -m mw4agent agent run \
  --message "请根据当前目录结构给出下一步建议" \
  --with-gateway-ls \
  --ls-path "." \
  --url http://127.0.0.1:18790
```

行为：

1. CLI 先通过 `GatewayLsTool` 调用 Gateway 的 `ls` RPC；
2. 把目录列表以文本形式塞进一个增强的 `extraSystemPrompt`；
3. 再调用 Gateway 的 `agent` + `agent.wait` 跑完整的 LLM 回合；
4. 最终在终端输出运行状态（或 JSON）。

---

## 4. `channels` 命令组（运行通道）

`channels` 子命令负责启动不同的通道 monitor，并将入站消息统一交给 `ChannelDispatcher` → `AgentRunner`。

### 4.1 console 通道（本地 stdin/stdout）

```bash
python -m mw4agent channels console run \
  --session-file mw4agent.sessions.json
```

启动后：

- 你可以在终端输入一行文本，按回车；
- console channel 会构造一个 `InboundContext`，通过 dispatcher 交给 `AgentRunner`；
- Agent 回复会以 `[AI] ...` 的形式打印到 stdout。

这是目前最简单的“本地聊天”方式，适合验证 AgentRunner/LLM/tool-call 是否工作正常。

### 4.2 Telegram 通道（长轮询）

```bash
export TELEGRAM_BOT_TOKEN="你的 Bot Token"

python -m mw4agent channels telegram run \
  --session-file mw4agent.sessions.json
```

- **`--bot-token`**：可显式传入，也可通过环境变量 `TELEGRAM_BOT_TOKEN` 提供；
- **`--session-file`**：会话存储文件，与 Gateway 共用同一格式。

### 4.3 Webhook 通道（泛用 HTTP Webhook）

```bash
python -m mw4agent channels webhook run \
  --host 0.0.0.0 \
  --port 8080 \
  --path /webhook \
  --session-file mw4agent.sessions.json
```

常见场景：从第三方系统（CI/CD、监控、业务系统）以 HTTP POST 的方式推消息进来，再由 Agent 处理。

### 4.4 Feishu 通道（Webhook / WebSocket）

将飞书应用凭证写入根配置（`channels.feishu`），供 Gateway 或 `feishu run` 使用：

```bash
mw4agent channels feishu add --app-id <APP_ID> --app-secret <APP_SECRET>
# 可选：--connection-mode webhook|websocket；--json 输出摘要（密钥脱敏）
# 凭证也可来自环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET；未传参时会提示输入。
```

为 **feishu-docs** 等 MCP 文档工具准备用户访问令牌（设备授权，落盘 `~/.mw4agent/feishu_oauth.json`）：

```bash
mw4agent feishu authorize
# 多账号：mw4agent feishu authorize --account <key>
# 状态：mw4agent feishu oauth-status
```

或在 **飞书机器人会话** 中发送 `/mw4auth`、`/feishu_auth` 或 `飞书授权`，机器人会推送授权卡片（与 CLI 同属设备码流，令牌按用户 open_id 落盘）。

Webhook 模式运行示例（独立进程）：

```bash
python -m mw4agent channels feishu run \
  --mode webhook \
  --host 0.0.0.0 \
  --port 8081 \
  --path /feishu/webhook \
  --session-file mw4agent.sessions.json
```

当前 WebSocket 模式通过官方 `lark-oapi` SDK 建立长连接，详情见 Feishu 通道架构文档。

---

## 5. `config` 命令组（配置段读写）

所有配置项（llm、skills、channels 等）**默认统一存储**在单一文件 `~/.mw4agent/mw4agent.json` 中。`config` 子命令用于读写该文件中的**各个段（section）**。

### 5.1 默认配置文件

- **路径**：`~/.mw4agent/mw4agent.json`
- **结构**：顶层键为配置段名，例如 `llm`、`skills`、`channels` 等，每段为一个 JSON 对象。
- **加密**：若已配置加密（`MW4AGENT_SECRET_KEY` 等），整个文件会按加密框架存储；否则为明文 JSON。

### 5.2 读取配置段

```bash
mw4agent config read llm
```

- 从 `~/.mw4agent/mw4agent.json` 中读取 `llm` 段并输出；
- 若该段不存在则输出 `{}`。

输出单行原始 JSON：

```bash
mw4agent config read llm --raw
```

也可读取其它段，如 `skills`、`channels`（若文件中已有对应键）。

### 5.3 写入配置段

从文件写入：

```bash
mw4agent config write llm --input llm.json
```

从 stdin 写入：

```bash
echo '{"provider":"openai","model_id":"gpt-4o-mini"}' | mw4agent config write llm --stdin
```

写入会**合并**到现有 `~/.mw4agent/mw4agent.json` 中：仅更新指定段，其它段保持不变。

---

## 6. `configuration` 命令组（交互式配置向导）

`configuration` 命令组用于交互式编辑 **全局配置文件**（与 `config read/write` 同一文件）：

- 路径：`~/.mw4agent/mw4agent.json`（llm、skills、channels 等均存于此文件）
- 内容示例：

```json
{
  "llm": {
    "provider": "vllm",
    "model_id": "your-model-id",
    "base_url": "http://127.0.0.1:8000",
    "api_key": "your-api-key"
  }
}
```

后续会在此文件中逐步加入 `channels`、`skills` 等子配置。

### 6.1 交互式配置（推荐）

直接运行：

```bash
mw4agent configuration
```

会启动一个简单的交互式向导：

- 显示当前的 LLM 配置（如果已存在）；
- 询问是否现在配置 LLM；
- 让你从 `vllm` / `aliyun-bailian` 中选择 provider，并输入/确认 `model_id`；
- 选择是否配置 `base_url`（例如本地 vLLM 代理地址）；
- 选择是否配置 `api_key`（例如云服务的鉴权 token）；
- 保存结果到 `~/.mw4agent/mw4agent.json`。

该向导目前只配置 LLM，**channels / skills 的交互配置位已预留**，将在后续版本中补充。

### 6.2 非交互式配置 LLM（脚本友好）

如果你希望在脚本或 CI 中直接写入 LLM 配置，可以使用：

```bash
mw4agent configuration set-llm \
  --provider vllm \
  --model-id your-model-id \
  --base-url http://127.0.0.1:8000 \
  --api-key your-api-key
```

或：

```bash
mw4agent configuration set-llm \
  --provider aliyun-bailian \
  --model-id your-aliyun-model
```

配置会被写入到 `~/.mw4agent/mw4agent.json` 的 `llm` 段，并受加密框架控制（参考加密文档）。

### 6.3 查看当前根配置

```bash
mw4agent configuration show
```

输出：

- 根配置文件路径；
- 当前 LLM provider / model-id 概要。

如果希望查看完整 JSON：

```bash
mw4agent configuration show --json
```

---

## 7. `configuration auth`：工具权限（tools policy）配置

`configuration auth` 子命令用于配置和检查 **tools 权限策略**（全局 / 按 channel / 按 user / 按 channel+user），底层写入 `~/.mw4agent/mw4agent.json` 中的 `tools` 段，与 Runner 中的 `ToolPolicyConfig` / `resolve_effective_policy_for_context` 保持一致。

### 7.1 交互式向导：`configuration auth wizard`

最推荐的方式是用交互向导一步步配置：

```bash
mw4agent configuration auth wizard
```

向导流程大致为：

- 选择 **scope**：
  - `global`：全局默认（`tools.profile/allow/deny`）；
  - `by_channel`：按 channel（如 feishu/telegram/console/webhook）；
  - `by_user`：按 user（区分 owner/user）；
  - `by_channel_user`：按 channel+user 组合（最高优先级）。
- 根据 scope 递进式询问：
  - `channel`（例如 `feishu`）；
  - `user-id`（例如 Feishu open_id、Telegram user id 等）；
  - 是否 owner（owner/user）。
- 选择 **profile**：`minimal` / `coding` / `full`；
- 可选多次添加 **allow / deny** 规则（工具名或 glob，如 `write`、`memory_*`）；
- 展示预览（当前 `tools` 段 JSON），最后确认是否写入。

写入结果示例：

```jsonc
{
  "tools": {
    "profile": "coding",
    "by_channel": {
      "feishu": { "profile": "coding", "deny": ["write", "memory_write"] }
    },
    "by_channel_user": {
      "feishu:ou_owner": { "profile": "full" }
    }
  }
}
```

### 7.2 非交互配置：`configuration auth set`

当你想脚本化或一次性写入某个 scope 时，可以使用非交互命令：

```bash
mw4agent configuration auth set \
  --scope by_channel \
  --channel feishu \
  --profile coding \
  --deny write \
  --deny memory_write
```

常用参数：

- `--scope`：`global` / `by_channel` / `by_user` / `by_channel_user`；
- `--channel`：scope 为 `by_channel`/`by_channel_user` 时必填；
- `--user-id`：scope 为 `by_user`/`by_channel_user` 时必填；
- `--owner/--user`：scope 为 `by_user` 时区分 owner:user 前缀；
- `--profile`：`minimal` / `coding` / `full`；
- `--allow`：追加 allow 规则（可多次出现）；
- `--deny`：追加 deny 规则（可多次出现）；
- `--clear-allow` / `--clear-deny`：先清空对应列表再追加。

### 7.3 查看当前 tools 策略：`configuration auth show`

```bash
mw4agent configuration auth show
```

- 默认以 JSON pretty-print 输出 `tools` 段；
- 加 `--json` 时只输出 JSON，方便和其它工具联动：

```bash
mw4agent configuration auth show --json
```

### 7.4 计算某个上下文的“有效权限”：`configuration auth effective`

为了确认某个 channel / user / owner 组合下**最终能用哪些工具**，可以运行：

```bash
mw4agent configuration auth effective \
  --channel feishu \
  --user-id ou_xxx \
  --owner \
  --authorized \
  --json
```

输出示例：

```json
{
  "context": {
    "channel": "feishu",
    "user_id": "ou_xxx",
    "owner": true,
    "authorized": true
  },
  "effectivePolicy": {
    "profile": "coding",
    "allow": ["memory_search"],
    "deny": ["write", "memory_write"]
  },
  "allowedTools": ["read", "memory_search", "memory_get", "memory_write"]
}
```

- `effectivePolicy`：Runner 实际使用的 ToolPolicy（profile/allow/deny 合并后的结果）；
- `allowedTools`：在 `profile` + `allow/deny` + `owner_only` 过滤后，最终暴露给 LLM 的工具列表。

---

## 8. `dashboard` 命令（Web 控制台）

`dashboard` 命令用于快速打开基于浏览器的 **MW4Agent 控制台**，它通过：

- `POST /rpc` 调用 Gateway 的 RPC（如 `agent`、`agents.list`、`config.*` 等）；
- `WS /ws` 订阅 Agent 事件流（assistant 文本流、生命周期事件等）；
- 前端是一个单页应用（SPA），由 Gateway 在根路径 `/` 提供静态资源。

### 7.1 启动 Gateway

在使用 `dashboard` 之前，需要先启动 Gateway（推荐仅绑定本机）：

```bash
mw4agent gateway run \
  --bind 127.0.0.1 \
  --port 18790
```

此时：

- HTTP 地址：`http://127.0.0.1:18790/`
- RPC：`POST http://127.0.0.1:18790/rpc`
- WebSocket：`ws://127.0.0.1:18790/ws`

### 7.2 打开 Dashboard

在另一个终端运行：

```bash
mw4agent dashboard
```

- 默认会假定 Gateway 运行在 `http://127.0.0.1:18790`；
- 命令行会打印 Dashboard 地址，并尝试用系统默认浏览器打开该链接；
- 如果浏览器无法自动打开，你可以手动复制链接到浏览器。

也可以显式指定 Gateway 地址：

```bash
mw4agent dashboard --url http://127.0.0.1:18790
```

或仅打印 URL，不自动打开浏览器：

```bash
mw4agent dashboard --no-open
```

### 8.3 当前 Dashboard 能做什么（骨架版）

当前版本的 Dashboard 是一个 **骨架实现**，主要用于验证端到端链路是否打通：

- 左侧是一个最小聊天面板：
  - 在输入框中输入消息点击发送；
  - 前端通过 `/rpc` 调用 `agent` 方法触发一次 Agent 运行；
  - Gateway 将 LLM 输出通过 `/ws` 以事件流形式推送回前端；
  - 前端把 assistant 文本消息渲染到聊天窗口。
- 右侧是 Gateway 状态面板：
  - 展示 WebSocket 是否已连接；
  - 展示最近一次运行的 `runId` 与收到的事件总数；
  - **Agents** 标签：调用 `agents.list`，列出各 Agent 的目录配置、`agentId` 与本 Gateway 进程内的运行状态（空闲 / 运行中、最近完成的 run）。
- **Config** 标签：分区查看/编辑根配置 `mw4agent.json`。

后续可以在这个骨架基础上扩展：

- 增加会话列表、通道状态、技能面板、定时任务（cron）管理等；
- 对齐 OpenClaw Dashboard 的多面板设计与操作能力。

---

## 9. `tools` 命令组（工具可见性与限制）

`tools` 命令用于检查**当前注册的所有工具**，以及在指定上下文下（channel/user/owner）是否启用。

### 9.1 列出工具（文本）

```bash
mw4agent tools list \
  --channel console \
  --user-id local \
  --no-owner
```

输出会包含：

- 工具名（如 `read`、`write`、`exec`、`process`）
- 当前是否可用（`ENABLED` / `DISABLED`）
- 是否 `owner_only`
- 限制原因（例如 `blocked by tools policy`、`owner_only`）

### 9.2 列出工具（JSON，推荐用于自动化）

```bash
mw4agent tools list \
  --channel feishu \
  --user-id ou_xxx \
  --owner \
  --authorized \
  --json
```

返回结构包含：

- `context`：本次计算使用的上下文
- `effectivePolicy`：生效后的 tools 策略（`profile/allow/deny`）
- `tools[]`：每个工具的启用状态与限制原因

---

## 10. 小结

- 使用 `gateway run` + `channels console run` 可以在本机快速搭建一个“Gateway + Console Chat”的测试环境；
- 使用 `agent run` 可以脚本化触发单次 Agent 回合（支持在调用前执行工具）；
- 使用 `config read/write` 可以安全地管理加密配置（LLM provider、通道配置等），避免手工处理加密细节；
- 使用 `configuration` 可以以交互式或非交互式方式配置全局 LLM 与后续的 channels/skills 设置；
- 使用 `tools list` 可以快速确认“当前有哪些工具可用、哪些被策略或 owner_only 限制”。

后续可以在 `docs/manuals/` 下为不同通道、不同运行模式补充更详细的 CLI 示例（如与 mock LLM server 联动的完整演示），以及为 `dashboard` / `configuration auth` 补充更丰富的使用说明（多面板、会话管理、通道控制、权限模板等）。 

