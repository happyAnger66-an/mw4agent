# feishu-docs 插件

在 MW4Agent 中注册三个 Agent 工具，通过**飞书官方 MCP**（与 [feishu-openclaw-plugin](https://github.com/clawbot-plugins/feishu-openclaw-plugin) 中 `fetch-doc` / `create-doc` / `update-doc` 相同协议）读写云文档。

## 启用

1. 将**父目录** `plugins` 加入扫描路径（二选一）：
   - `export MW4AGENT_PLUGIN_DIR=/path/to/mw4agent/plugins`
   - 或在 `~/.mw4agent/mw4agent.json` 的 `plugins.plugin_dirs` 中写入该路径  
2. 启动 Gateway：`mw4agent gateway run`（会 `load_plugins()`）。

若使用 `plugins_enabled` 白名单，插件名为 **`feishu-docs`**（见 `plugin.json`）。

## 推荐：设备授权落盘（与 OpenClaw 体验接近）

1. 已在 `mw4agent.json`（或环境变量）配置好飞书应用的 **`app_id` / `app_secret`**（与通道机器人相同应用即可）。
2. 在飞书开放平台为该应用开通文档/wiki 等相关 **用户态 scope**（与 MCP 文档能力一致，可参考 feishu-openclaw-plugin 的 scope 列表），并确保支持 **OAuth 2.0 设备授权**（Device Authorization Grant）。
3. 本机执行：
   ```bash
   mw4agent feishu authorize
   ```
   按提示在浏览器完成授权后，**access_token / refresh_token** 会写入 **`~/.mw4agent/feishu_oauth.json`**（权限 `0600`）。`feishu-docs` 插件会自动读取并在过期前 **refresh**。
4. 多账号时：`channels.feishu.accounts` 下用 `--account <key>`；或用环境变量 **`FEISHU_OAUTH_APP_ID`** 指定要用哪套 `app_id` 的令牌。
5. 国际版 Lark：`mw4agent feishu authorize --brand lark`（或配置 `api_base` 含 `larksuite` 时插件侧会倾向 `lark` 端点）。

其他子命令：`feishu oauth-status`、`feishu revoke-local`（仅删本地文件，不向飞书服务端 revoke）。

### 在飞书机器人会话里授权（卡片，对齐 OpenClaw 思路）

在与机器人对话中发送以下**整行**指令（勿与其它文字混在同一行，除非仅指令本身）：

- `/mw4auth`、`/feishu_auth`、`/feishuauth`
- 或中文：`飞书授权`、`文档授权`

机器人会推送一条**消息卡片**（含「前往授权」按钮，打开设备授权页），后台轮询完成后把 **UAT 按你的 open_id 写入** `feishu_oauth.json`（与 CLI 的 `__default__` 槽位区分）。之后在飞书里触发的 Agent 工具会优先使用该用户的令牌。

需已配置 Webhook/WS 通道且应用具备 **发消息 / 消息卡片** 等权限。

## 与 OpenClaw 插件是否「一样需要令牌」？

**一样需要用户访问令牌（UAT）。**  
feishu-openclaw-plugin 里的 `create-doc` / `fetch-doc` / `update-doc` 走的是飞书 **MCP**，实现上通过 `callMcpTool` 发送 `X-Lark-MCP-UAT`（见该仓库 `src/tools/mcp/shared.js`）。OpenClaw 在用户完成 **OAuth / Device Flow** 后把 UAT 写入本地，执行工具时由 `ToolClient.invoke(..., { as: "user" })` **自动带上**，所以模型通常不会说「请提供你的令牌」。  

MW4Agent 已通过 **`mw4agent feishu authorize`** 与 **飞书会话内 `/mw4auth` 卡片** 提供用户授权入口；若均未配置，则需 **环境变量或配置明文 UAT**，否则工具会失败。

通道里配置的 **`app_id` / `app_secret` 是租户/机器人凭证**，用于发消息等 **tenant_access_token** 能力，**不能**代替 MCP 文档接口所需的 **UAT**。

## 鉴权（用户访问令牌 UAT）

MCP 要求 **用户态** token（请求头 `X-Lark-MCP-UAT`），**不是** 飞书应用机器人的 `app_secret`。

配置方式：

- 环境变量：`FEISHU_MCP_UAT`（或 `LARK_MCP_UAT`、`FEISHU_USER_ACCESS_TOKEN`）
- 配置文件：`channels.feishu.mcp_user_access_token`（或 `user_access_token`、`mcp_uat`）

可选：`FEISHU_MCP_ENDPOINT`（默认 `https://mcp.feishu.cn/mcp`）、`FEISHU_MCP_BEARER_TOKEN`。

## 工具

- `feishu_fetch_doc` — 对应 MCP `fetch-doc`
- `feishu_create_doc` — 对应 MCP `create-doc`
- `feishu_update_doc` — 对应 MCP `update-doc`

## Skills

插件自带 `skills/feishu-docs/SKILL.md`，加载后技能名为 **feishu-docs**（来源 `plugin`）。

## LLM 可调用的前提

1. **策略**：默认 `tools.profile` 为 `coding` 时已包含 **`feishu_*`** glob，飞书文档三个工具会进入 LLM 的 tool 列表（见 `mw4agent/agents/tools/policy.py`）。若你改过 `tools.allow` / `by_channel` 等覆盖了 profile，请自行加上 `feishu_*` 或 `tools.profile: full`。
2. **禁用**：若不想暴露飞书工具，在 `tools.deny` 中加入 `feishu_*`。
3. **插件必须先被 Gateway 加载**：需配置 `MW4AGENT_PLUGIN_DIR` 或 `plugins.plugin_dirs`，否则 registry 里没有这些工具，模型仍看不到。
