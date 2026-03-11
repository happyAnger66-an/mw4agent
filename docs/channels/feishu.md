# MW4Agent Feishu 通道设计与实现规划

本文档基于 `feishu-openclaw-plugin` 的架构，设计在 MW4Agent 中实现 Feishu 通道的 Python 版本方案，并给出分阶段 TODO 列表。

---

## 一、整体架构设计（对标 OpenClaw 插件）

### 1.1 端到端链路

```text
Feishu HTTP 事件回调（飞书开放平台）
    ↓
FastAPI Webhook 接收层（/feishu/webhook）
    ↓ 解析 & 标准化
构造 InboundContext(channel="feishu", ...)
    ↓
ChannelDispatcher.dispatch_inbound()
    ↓
AgentRunner.run()  →  LLM / tools / 事件流
    ↓
AgentRunResult.payloads
    ↓ 映射
OutboundPayload(text=..., extra={"inbound": {...}})
    ↓
FeishuChannel.deliver()  → 调用 Feishu Open API 发送消息/卡片
```

### 1.2 关键组件设计

- **`mw4agent/channels/plugins/feishu.py`**
  - `FeishuChannel(ChannelPlugin)`：
    - `id="feishu"`
    - `meta = ChannelMeta(id="feishu", label="Feishu", docs_path="/channels/feishu")`
    - `capabilities = ChannelCapabilities(chat_types=("direct","group","channel","thread"), native_commands=True, block_streaming=False)`
    - `dock = ChannelDock(id="feishu", capabilities=..., resolve_require_mention=lambda acct: True)`（群聊默认需要 @）
  - `run_monitor(on_inbound)`：
    - 启动 FastAPI + Uvicorn 的 Feishu 事件接收服务（可重用现有 FastAPI 实例）；
    - 暴露 `POST /feishu/webhook`：
      - 处理 URL 验证（challenge）；
      - 解析 Feishu message event，将其映射为 `InboundContext`：
        - `channel="feishu"`
        - `text`：从消息内容中抽取文本（必要时用 helper 拼装）
        - `session_key=f"feishu:{chat_id}"`
        - `session_id=str(chat_id)`
        - `agent_id="main"`
        - `chat_type`：从 Feishu 的 `chat_type` 映射为 `"direct" | "group" | "channel" | "thread"`
        - `was_mentioned` / `command_authorized`：根据 @Bot / 指令解析结果设置
        - `extra={"chat_id":..., "message_id":..., "sender_open_id":..., "raw_event": event}`
      - 使用 `asyncio.create_task(on_inbound(ctx))` 调用 `ChannelDispatcher`，HTTP 端快速返回。
  - `deliver(payload)`：
    - 从 `payload.extra["inbound"]["extra"]` 中取得 `chat_id` / `reply_to_message_id` / `thread_id` / `account_id`；
    - 根据 `payload.text`、媒体信息、`payload.extra.get("feishu", {}).get("card")` 决定发送路径：
      - 文本 → `feishu_outbound.send_text(...)`
      - 媒体 → `feishu_outbound.send_media(...)`
      - 卡片 → `feishu_outbound.send_payload(...)`
    - 出错时记录日志或写入事件流。

- **`mw4agent/channels/feishu_outbound.py`（新文件）**
  - 角色：对标 JS 插件中的 `feishuOutbound`，是通道级的统一出站适配器。
  - 能力：
    - `async send_text(cfg, to, text, account_id=None, reply_to_id=None, thread_id=None, mentions=None)`：
      - 处理 mention 前缀；
      - 调用底层 Feishu 客户端 `client.send_text(...)`；
    - `async send_media(cfg, to, text, media_url, media_local_roots=None, ...)`：
      - 如有文本先发文本，再发媒体；
    - `async send_payload(cfg, to, payload, ...)`：
      - 支持：
        - 纯文本；
        - 文本 + 媒体（多 mediaUrls 时循环发送）；
        - 带 `payload.channel_data["feishu"]["card"]` 的卡片消息：
          - 文本 + card + 媒体组合发送，并聚合 warning 信息。

- **`mw4agent/feishu/client.py`（推荐新子包）**
  - 角色：对标 JS 中的 `LarkClient`，封装 Feishu Open API。
  - 主要接口：
    - `class FeishuClient` / 一组函数：
      - `async send_text(chat_id, text, reply_to_message_id=None, thread_id=None, account_id=None)`
      - `async send_card(chat_id, card_json, reply_to_message_id=None, thread_id=None, account_id=None)`
      - `async send_media(chat_id, media_url, ...)`
  - 配置：
    - 从 mw4agent 配置或环境变量中读取 `app_id/app_secret/encrypt_key/verification_token`；
    - 先实现最小的 `tenant_access_token` 获取与缓存，后续再增强为完整 OAuth 流程。

- **CLI 集成：`mw4agent/cli/channels/register.py`**
  - 新子命令：
    - `mw4agent channels feishu run-webhook`：
      - 选项：`--session-file`、`--host`、`--port`、`--path=/feishu/webhook`、`--app-id`、`--app-secret` 等；
      - 流程：
        - `get_channel_registry().register_plugin(FeishuChannel(...))`
        - 构造 `SessionManager` / `AgentRunner` / `ChannelDispatcher`；
        - `dispatcher.run_channel("feishu")` 启动 webhook 监听。

---

## 二、分阶段 TODO 列表

### 阶段 1：基础通道打通（文本单轮）

1. **配置与依赖**
   - [ ] 确认 Feishu API 调用方案（先用 `httpx` + tenant_access_token，暂不实现完整事件签名校验）。
   - [ ] 在 `setup.py` 中确认/补充 Feishu 所需依赖（`httpx`、如需可加 `python-jose` 等）。

2. **Feishu 客户端封装**
   - [ ] 新建 `mw4agent/feishu/client.py`：
     - 提供 `send_text` / `send_card` / `send_media` 三个最小接口；
     - 支持通过 app_id/app_secret 获取 tenant_access_token，并做简单缓存。
   - [ ] 为客户端添加基础日志与错误输出，方便排查。

3. **出站适配器**
   - [ ] 新建 `mw4agent/channels/feishu_outbound.py`：
     - `send_text`：处理 mention 前缀（可先简化），然后调 `FeishuClient.send_text`；
     - `send_media`：先发文本再发媒体，或在无 mediaUrl 时回退到文本；
     - `send_payload`：对标 JS 插件 `feishuOutbound.sendPayload` 的最小子集。

4. **ChannelPlugin 实现**
   - [ ] 新建 `mw4agent/channels/plugins/feishu.py`：
     - 定义 `FeishuChannel` 的 `id/meta/capabilities/dock`；
     - 实现 `deliver`，从 `OutboundPayload.extra["inbound"]` 中读取 `chat_id` 等信息，调用 `feishu_outbound`。

5. **Dispatcher 出站信息对接**
   - [ ] 在 Feishu inbound handler 中，保证构造的 `InboundContext.extra` 至少包含：
     - `chat_id`、`message_id`、`thread_id`、`sender_open_id`；
   - [ ] 确认 `ChannelDispatcher.dispatch_inbound` 透传的 `OutboundPayload.extra["inbound"]` 足以支持 Feishu 出站逻辑。

### 阶段 2：入站 Webhook/事件接入

6. **FastAPI Webhook 入口**
   - [ ] 在 `FeishuChannel.run_monitor` 中：
     - 构建或重用 FastAPI 应用；
     - 注册 `POST /feishu/webhook` 路由；
     - 处理 Feishu 的 URL 验证（challenge）。

7. **事件解析与上下文映射**
   - [ ] 实现 `parse_feishu_message_event(event)`：
     - 返回 `(text, chat_id, message_id, thread_id, chat_type, sender_open_id)`；
   - [ ] 将这些字段映射为 `InboundContext`：
     - `chat_type` → `"direct" | "group" | "channel" | "thread"`；
     - `was_mentioned` → p2p 默认 True，群聊后续基于 mention 解析；
     - `command_authorized` → 后续挂钩指令/权限系统。

### 阶段 3：对标 OpenClaw 插件的增强能力

8. **多账号支持**
   - [ ] 设计 mw4agent 的 Feishu 多账号配置结构（对标 `cfg.channels.feishu.accounts`）；
   - [ ] 新建 `mw4agent/feishu/accounts.py`：
     - `get_feishu_account_ids(cfg)` / `get_feishu_account(cfg, account_id)` / `get_enabled_feishu_accounts(cfg)`；
     - 用于 `FeishuClient` 和 `FeishuChannel` 选择正确的账号（如 prod/uat）。

9. **mention/command 解析**
   - [x] 在 Feishu inbound 解析中：基于 `chat_type` + 文本包含 `@`/`＠` 做简易 `was_mentioned` 决策（直聊默认 True，群聊有 @ 才触发）。
   - [ ] 后续识别 @Bot、自定义命令（如 `/feishu_diagnose`），设置更精细的 `was_mentioned` 和 `command_authorized`；
   - [ ] 必要时增加 mention gating 策略，配合 `mention_gating.resolve_mention_gating`。

10. **卡片与富媒体支持**
    - [ ] 在 `feishu_outbound.send_payload` 中：
      - 支持 `payload.channel_data["feishu"]["card"]`；
      - 复刻“文本 + card + 多媒体”编排逻辑；
    - [ ] 提供 `build_markdown_card(text)` 帮助函数，对标 JS 的 `buildMarkdownCard`。

11. **OAuth/授权（中后期）**
    - [ ] 在 mw4agent 的工具系统中增加 `feishu_oauth` 工具：
      - 仅暴露安全动作（如 revoke），不返回 token 明文；
    - [ ] 参考 OpenClaw `executeAuthorize`：
      - 用 Python 实现 Device Flow 授权 + 授权卡片 + synthetic message 自动重试；
      - 授权状态可存入专用加密存储或 session metadata。

### 阶段 4：文档与测试

12. **文档**
   - [x] 在本文件基础上记录端到端链路与实现规划，并随着实现进展更新勾选状态。

13. **测试**
   - [x] 添加基础测试用例：
     - `tests/test_feishu_channel_basic.py`：验证 `FeishuChannel.deliver` 在缺失 `chat_id` 时会打印到 stdout；
   - [ ] 后续完善：
     - 单元测试：`FeishuClient` 的 API 调用（mock httpx）、`feishu_outbound` 的编排逻辑；
     - 集成测试：模拟 `POST /feishu/webhook`，验证 `ChannelDispatcher` + `AgentRunner` 的联动；
     - 出站测试：在无真实 Feishu 环境下通过 mock 确认 `deliver` 调用路径是否正确。
