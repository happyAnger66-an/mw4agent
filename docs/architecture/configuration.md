# MW4Agent 配置架构说明

本文描述 MW4Agent 的**统一根配置**与 **Configuration CLI** 的实现：配置项（LLM provider、Channels）的存储格式、交互向导与子命令、以及运行时如何读取。

## 1. 配置文件位置与格式

- **路径**：默认 `~/.mw4agent/mw4agent.json`；可通过环境变量 `MW4AGENT_CONFIG_DIR` 指定目录（如测试或多环境）。
- **格式**：单一 JSON 文件，顶层按**区块（section）**组织，当前支持的区块包括：
  - **`llm`**：LLM 提供商与模型（provider、model_id、base_url、api_key 等）。
  - **`channels`**：各 channel 的配置（当前支持 feishu、console）。
  - **`skills`**：技能目录的全局过滤与注入上限（`filter`、`limits` 等）；每智能体还可在 `~/.mw4agent/agents/<id>/agent.json` 中配置 `skills` 与全局 **取交集**（方案 B）。详见 [manuals/configuration.md](../manuals/configuration.md) 中「skills」一节。

示例：

```json
{
  "llm": {
    "provider": "openai",
    "model_id": "gpt-4o-mini",
    "base_url": "https://api.openai.com",
    "api_key": "sk-..."
  },
  "channels": {
    "feishu": {
      "app_id": "cli_xxx",
      "app_secret": "...",
      "connection_mode": "webhook"
    },
    "console": {}
  }
}
```

加密存储由 `ConfigManager` + `EncryptedFileStore` 负责；未配置密钥时回退为明文（见 [crypto/encryption-framework.md](crypto/encryption-framework.md)）。

## 2. 配置项选择（LLM vs Channels）

Configuration CLI 将 **LLM 配置** 与 **Channels 配置** 放在同一套流程中，用户可任选其一或依次配置。

### 2.1 交互向导（无子命令）

执行：

```bash
mw4agent configuration
```

流程为：

1. **选择要配置的区块**  
   - 列表选项：**LLM provider**、**Channels**（由 questionary 下拉选择；无 questionary 时用数字 1/2 选择）。
2. **若选「LLM provider」**  
   - 按提示选择 provider（echo / openai / deepseek / vllm 等）、model_id、base_url、api_key，写入 `llm` 段。
3. **若选「Channels」**  
   - 再选 channel：**feishu** 或 **console**。  
   - **feishu**：提示输入 Feishu App ID、App Secret，写入 `channels.feishu`。  
   - **console**：仅提示为内置 channel，无需凭证。
4. **若选「Agent LLM (per-agent override)」**（`mw4agent/cli/configuration.py` 中 `agent_llm`）  
   - 从已有 agent 列表中选一个（或输入 agent id）。  
   - 依次配置 provider、model id、base URL、API key，写入 `~/.mw4agent/agents/<agentId>/agent.json` 的 `llm` 字段；与根配置 `llm` 合并规则见 [multi_agents.md](multi_agents.md)。  
5. 询问「Configure another section?」  
   - 选是则回到步骤 1，否则退出。

所有修改合并写入同一份 `mw4agent.json`。

### 2.2 子命令（仅配置某一区块）

- **`configuration set-llm`**：只更新 LLM 配置（--provider、--model-id、--base-url、--api-key）。
- **`configuration set-channels`**：只更新 Channels 配置（见下节）。
- **`configuration show`**：打印当前根配置（人类可读）；`--json` 输出完整 JSON。

## 3. Channels 配置内容

当前支持的 channel 与配置项：

| Channel   | 说明           | 可配置项                          |
|----------|----------------|-----------------------------------|
| **feishu** | 飞书 WebSocket/API | `app_id`、`app_secret`            |
| **console** | 内置控制台       | 无（仅占位 `console: {}` 或省略） |

### 3.1 Feishu

- **配置键（单应用，兼容旧版）**：`channels.feishu.app_id`、`channels.feishu.app_secret`、`channels.feishu.connection_mode`（可选，`webhook` | `websocket`，默认 `webhook`）、可选 `channels.feishu.agent_id`（绑定默认 agent，默认 `main`）。
- **多应用**：`channels.feishu.accounts` 为对象，键为逻辑名（如 `sales`、`support`），值为该应用的字段（可覆盖父级 `feishu` 中除 `accounts` 外的公共默认值）。每个应用可选：
  - `agent_id`：该飞书应用入站消息路由到的 Agent；
  - `webhook_path`：Webhook 模式下的 HTTP 路径（多应用时默认 `/feishu/webhook/<逻辑名>`，需在飞书后台分别配置）；
  - `encrypt_key` / `verification_token`：WebSocket 模式鉴权相关。
- **Gateway**：解析 `list_feishu_accounts()`（见 `mw4agent/channels/feishu_accounts.py`）中的**全部**有效账号并注册；`webhook` 账号各自挂载路由，`websocket` 账号在 lifespan 内各启一条连接。
- **工具策略**：入站 `channel` 为 `feishu:<逻辑名>` 时，若 `tools.by_channel` 无对应项，会**回退**到 `by_channel.feishu`（见 `agents/tools/policy.py`）。
- **CLI**：
  ```bash
  mw4agent configuration set-channels --channel feishu --app-id <APP_ID> --app-secret <APP_SECRET>
  mw4agent channels feishu add --account sales --app-id <A> --app-secret <S> --agent-id my_agent
  ```
- **运行时**：Feishu 插件（多应用）使用构造时注入的 `FeishuConfig`；`FeishuClient` 在无显式配置时仍可从环境变量或顶层 `channels.feishu` 读取（单应用场景）。

### 3.2 Console

- 无需凭证，CLI 中选「console」仅提示为内置；`configuration show` 中显示为 `console : (built-in, no credentials)`。

### 3.3 随 Gateway 启动（OpenClaw 行为）

若已配置 `channels.feishu`（或设置了环境变量），**启动 Gateway 时会按配置自动启用 Feishu（含 `accounts` 下全部账号）**，无需再单独执行 `mw4agent channels feishu run`：

- **`connection_mode` 为 `webhook`（默认）**：在同一进程中挂载 Webhook 路由；单应用默认路径 `/feishu/webhook`；多应用为各账号路径（默认 `/feishu/webhook/<逻辑名>` 或自定义 `webhook_path`）。
- **`connection_mode` 为 `websocket`**：在进程 lifespan 中按账号后台启动 lark-oapi 长连接。

实现见 `mw4agent/gateway/server.py` 中 `create_app()` 内 `list_feishu_accounts` + 循环注册 `FeishuChannel`。

## 4. 实现位置与读取方式

- **根配置读写**：`mw4agent/config/root.py`（`read_root_config`、`write_root_config`、`read_root_section`、`write_root_section`）。
- **Configuration CLI**：`mw4agent/cli/configuration.py`  
  - 常量：`SUPPORTED_CHANNELS = ["feishu", "console"]`；向导选项含 **LLM provider (global)**、**Agent LLM (per-agent)**、**Channels**（见 `CONFIG_SECTION_CHOICES`）。  
  - 向导：`_run_interactive_wizard` → `_prompt_config_section` → `_run_llm_config` / `_run_channels_config`。  
  - 写回：`_update_llm_section`、`_update_channels_section` 合并后 `write_root_config`。
- **Channels 运行时读取**：  
  - `mw4agent/channels/plugins/feishu.py`（WebSocket 模式）：缺省时从 `channels.feishu` 取 app_id、app_secret。  
  - `mw4agent/feishu/client.py`（FeishuClient）：同上。

## 5. 相关文档

- LLM  provider 与 backends 解析优先级：[llm/provider_config.md](llm/provider_config.md)
- 配置加密与 ConfigManager：[crypto/encryption-framework.md](crypto/encryption-framework.md)
- CLI 入口与注册：[cli/README.md](cli/README.md)
