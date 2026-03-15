# MW4Agent 配置架构说明

本文描述 MW4Agent 的**统一根配置**与 **Configuration CLI** 的实现：配置项（LLM provider、Channels）的存储格式、交互向导与子命令、以及运行时如何读取。

## 1. 配置文件位置与格式

- **路径**：默认 `~/.mw4agent/mw4agent.json`；可通过环境变量 `MW4AGENT_CONFIG_DIR` 指定目录（如测试或多环境）。
- **格式**：单一 JSON 文件，顶层按**区块（section）**组织，当前支持的区块：
  - **`llm`**：LLM 提供商与模型（provider、model_id、base_url、api_key 等）。
  - **`channels`**：各 channel 的配置（当前支持 feishu、console）。

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
4. 询问「Configure another section?」  
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

- **配置键**：`channels.feishu.app_id`、`channels.feishu.app_secret`、`channels.feishu.connection_mode`（可选，`webhook` | `websocket`，默认 `webhook`）。
- **CLI**：
  ```bash
  mw4agent configuration set-channels --channel feishu --app-id <APP_ID> --app-secret <APP_SECRET>
  ```
- **运行时**：Feishu 插件（WebSocket 模式）与 `FeishuClient` 在**未设置环境变量** `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 时，会从 `read_root_section("channels")["feishu"]` 读取；环境变量优先于配置文件。

### 3.2 Console

- 无需凭证，CLI 中选「console」仅提示为内置；`configuration show` 中显示为 `console : (built-in, no credentials)`。

### 3.3 随 Gateway 启动（OpenClaw 行为）

若已配置 `channels.feishu`（或设置了环境变量），**启动 Gateway 时会按配置自动启用 Feishu**，无需再单独执行 `mw4agent channels feishu run`：

- **`connection_mode` 为 `webhook`（默认）**：在同一进程中挂载 Webhook 路由，事件订阅 URL 为 `http://<host>:<gateway_port>/feishu/webhook`。
- **`connection_mode` 为 `websocket`**：在进程 lifespan 中后台启动 lark-oapi 长连接，不占用 HTTP 路径。

实现见 `mw4agent/gateway/server.py` 中 `create_app()` 内对 `read_root_section("channels")` 的读取及按 `connection_mode` 分支（挂载 router 或 lifespan 中启动 websocket）。

## 4. 实现位置与读取方式

- **根配置读写**：`mw4agent/config/root.py`（`read_root_config`、`write_root_config`、`read_root_section`、`write_root_section`）。
- **Configuration CLI**：`mw4agent/cli/configuration.py`  
  - 常量：`SUPPORTED_CHANNELS = ["feishu", "console"]`，`CONFIG_SECTION_CHOICES = [("LLM provider", "llm"), ("Channels", "channels")]`。  
  - 向导：`_run_interactive_wizard` → `_prompt_config_section` → `_run_llm_config` / `_run_channels_config`。  
  - 写回：`_update_llm_section`、`_update_channels_section` 合并后 `write_root_config`。
- **Channels 运行时读取**：  
  - `mw4agent/channels/plugins/feishu.py`（WebSocket 模式）：缺省时从 `channels.feishu` 取 app_id、app_secret。  
  - `mw4agent/feishu/client.py`（FeishuClient）：同上。

## 5. 相关文档

- LLM  provider 与 backends 解析优先级：[llm/provider_config.md](llm/provider_config.md)
- 配置加密与 ConfigManager：[crypto/encryption-framework.md](crypto/encryption-framework.md)
- CLI 入口与注册：[cli/README.md](cli/README.md)
