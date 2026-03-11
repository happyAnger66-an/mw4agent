# OpenClaw Feishu 插件架构与代码逻辑分析

本文档分析 `feishu-openclaw-plugin` 这个 OpenClaw 飞书/Lark 插件的整体架构与核心代码逻辑，便于在 MW4Agent 中复刻 Feishu 通道和相关工具能力时参考。

> 参考文件：
>
> - 插件配置：`openclaw.plugin.json`
> - 插件入口：`index.js`
> - 核心模块示例：
>   - `src/channel/plugin.js`（通道适配器，未在本文展开源码）
>   - `src/messaging/outbound/outbound.js`
>   - `src/messaging/outbound/send.js`
>   - `src/core/accounts.js`
>   - `src/tools/oauth.js`

---

## 1. 插件清单与基本配置

### 1.1 插件声明（openclaw.plugin.json）

```json
{
  "id": "feishu-openclaw-plugin",
  "channels": ["feishu"],
  "skills": ["./skills"],
  "configSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {}
  }
}
```

- **id**：`feishu-openclaw-plugin`
- **channels**：声明此插件提供 `feishu` 通道
- **skills**：从 `./skills` 目录加载额外技能（如工具集合）
- **configSchema**：当前使用空对象 schema，实际配置结构由插件内部自行解析（如 `cfg.channels.feishu`）

真正被 OpenClaw 加载时，插件的“运行时定义”来自 `index.js` 的默认导出。

---

## 2. 顶层入口：插件注册与能力暴露

### 2.1 插件入口 index.js

`index.js` 是 OpenClaw 识别的主入口，职责：

- 定义插件对象（id/name/description/configSchema/register）
- 注册 Feishu 通道
- 注册 Feishu 相关工具（OAPI / MCP / OAuth）
- 暴露调试与消息发送相关的公共 API（供其他模块或 CLI 使用）

核心结构（概念性摘录）：

```ts
import { emptyPluginConfigSchema } from "openclaw/plugin-sdk";
import { feishuPlugin } from "./src/channel/plugin.js";
import { LarkClient } from "./src/core/lark-client.js";
import { registerOapiTools } from "./src/tools/oapi/index.js";
import { registerFeishuMcpDocTools } from "./src/tools/mcp/doc/index.js";
import { registerFeishuOAuthTool } from "./src/tools/oauth.js";
import { registerFeishuOAuthBatchAuthTool } from "./src/tools/oauth-batch-auth.js";
import { runDiagnosis, formatDiagReportCli, traceByMessageId, formatTraceOutput, analyzeTrace } from "./src/commands/diagnose.js";
import { registerCommands } from "./src/commands/index.js";
import { trace } from "./src/core/trace.js";

const plugin = {
  id: "feishu",
  name: "Feishu",
  description: "Feishu/Lark channel plugin with doc/wiki/drive/task/calendar tools",
  configSchema: emptyPluginConfigSchema(),
  register(api) {
    // 1) 绑定运行时
    LarkClient.setRuntime(api.runtime);

    // 2) 注册 Feishu 通道
    api.registerChannel({ plugin: feishuPlugin });

    // 3) 注册工具：Open API 工具、MCP Doc 工具、OAuth 工具等
    registerOapiTools(api);
    registerFeishuMcpDocTools(api);
    registerFeishuOAuthTool(api);
    registerFeishuOAuthBatchAuthTool(api);

    // 4) Tool 调用 trace 钩子
    api.on("before_tool_call", ...);
    api.on("after_tool_call", ...);

    // 5) 注册诊断 CLI 命令（feishu-diagnose）
    api.registerCli(...);

    // 6) 注册 chat 命令（/feishu_diagnose, /feishu_auth 等）
    registerCommands(api);
  },
};

export default plugin;
```

**要点：**

- 插件在 `register(api)` 方法中完成所有集成工作：
  - 挂载通道适配器（`feishuPlugin`）；
  - 注入工具（OAPI、MCP、OAuth）；
  - 注入 CLI 与聊天指令；
  - 接入 OpenClaw 的 tool 调用事件流（trace）。
- `LarkClient.setRuntime(api.runtime)` 把 OpenClaw 的 runtime 能力（如 Markdown chunk、表格转换）暴露给插件内部代码。

### 2.2 对外导出的辅助函数

`index.js` 还导出了大量实用方法，例如：

- `monitorFeishuProvider`：用于启动 Feishu 入站监控（webhook/事件回调）；
- `sendMessageFeishu`, `sendCardFeishu`, `editMessageFeishu` 等：底层消息发送封装；
- 媒体发送：`uploadImageLark`, `sendFileLark` 等；
- 反应（reaction）操作：`addReactionFeishu`, `removeReactionFeishu` 等；
- 入站解析器：`parseMessageEvent`, `checkMessageGate`, `handleFeishuReaction` 等。

这些函数构成了 Feishu 插件的“公共 API”，既可以被 OpenClaw 核心调用，也可以被其他插件/集成使用。

---

## 3. 通道出站层：feishuOutbound 与 sendMessageFeishu/sendCardFeishu

Feishu 插件对出站消息做了两层抽象：

1. **Adapter 级别**：`feishuOutbound`（通道级的统一出站适配器）
2. **底层发送函数**：`sendMessageFeishu`, `sendCardFeishu`, `editMessageFeishu` 等（直接调用 Lark SDK）

### 3.1 feishuOutbound：统一出站适配器

文件：`src/messaging/outbound/outbound.js`

```ts
import { LarkClient } from "../../core/lark-client.js";
import { sendTextLark, sendMediaLark, sendCardLark } from "./deliver.js";
import { trace } from "../../core/trace.js";

function resolveFeishuSendContext(params) {
  return {
    cfg: params.cfg,
    replyToMessageId: params.replyToId ?? undefined,
    replyInThread: Boolean(params.threadId),
    accountId: params.accountId ?? undefined,
  };
}

export const feishuOutbound = {
  deliveryMode: "direct",
  chunker: (text, limit) => LarkClient.runtime.channel.text.chunkMarkdownText(text, limit),
  chunkerMode: "markdown",
  textChunkLimit: 4000,

  async sendText({ cfg, to, text, accountId, replyToId, threadId }) { ... },
  async sendMedia({ cfg, to, text, mediaUrl, mediaLocalRoots, accountId, replyToId, threadId }) { ... },
  async sendPayload({ cfg, to, payload, mediaLocalRoots, accountId, replyToId, threadId }) { ... },
};
```

**关键点：**

- `chunker` 使用 `LarkClient.runtime.channel.text.chunkMarkdownText` 做 Markdown 文本切片，统一处理长度限制（`textChunkLimit=4000`）；
- `sendText`、`sendMedia`、`sendPayload` 都先通过 `resolveFeishuSendContext` 把 adapter 级参数规范化，再调用更底层的 `sendTextLark` / `sendMediaLark` / `sendCardLark`。
- `sendPayload` 支持：
  - 常规文本 + 媒体（多 mediaUrl 时逐个发送）；
  - `payload.channelData.feishu.card` 形式的 Feishu Card：
    - 先发文本，后发卡片，再发媒体；
    - 聚合所有 media 的告警信息到 `meta.warnings`。

这层适配器类似于 OpenClaw 其他通道（比如 Telegram、Slack）的 `...Outbound` 设计：提供**统一的出站接口**，屏蔽底层 API 差异。

### 3.2 底层发送逻辑：send.js

文件：`src/messaging/outbound/send.js`

主要职责：把“OpenClaw 语义级参数”转换成 Feishu IM API 调用。

#### 3.2.1 文本消息：sendMessageFeishu

```ts
import { LarkClient } from "../../core/lark-client.js";
import { normalizeFeishuTarget, resolveReceiveIdType } from "../../core/targets.js";
import { optimizeMarkdownStyle } from "../../card/markdown-style.js";
import { buildMentionedMessage, buildMentionedCardContent } from "../inbound/mention.js";
import { runWithMessageUnavailableGuard } from "../message-unavailable.js";

function normalizeMessageId(messageId) { ... }  // 处理 "om_xxx:auth-complete" 合成 ID

export async function sendMessageFeishu(params) {
  const { cfg, to, text, replyToMessageId, mentions, accountId, replyInThread } = params;
  const client = LarkClient.fromCfg(cfg, accountId).sdk;

  // 1. mention 处理（前缀拼接）
  let messageText = text;
  if (mentions && mentions.length > 0) {
    messageText = buildMentionedMessage(mentions, messageText);
  }

  // 2. Markdown 表格转换（可选）
  try {
    const runtime = LarkClient.runtime;
    if (runtime?.channel?.text?.convertMarkdownTables) {
      messageText = runtime.channel.text.convertMarkdownTables(messageText, "bullets");
    }
  } catch { /* 忽略 */ }

  // 3. Markdown 风格优化
  messageText = optimizeMarkdownStyle(messageText, 1);

  // 4. 构建 Feishu post 格式内容
  const contentPayload = JSON.stringify({
    zh_cn: { content: [[{ tag: "md", text: messageText }]] },
  });

  // 5. reply 情况：使用 message.reply，支持 threaded reply
  if (replyToMessageId) {
    const normalizedId = normalizeMessageId(replyToMessageId);
    const response = await runWithMessageUnavailableGuard({
      messageId: normalizedId,
      operation: "im.message.reply(post)",
      fn: () => client.im.message.reply({
        path: { message_id: normalizedId },
        data: { content: contentPayload, msg_type: "post", reply_in_thread: replyInThread },
      }),
    });
    return { messageId: response?.data?.message_id ?? "", chatId: response?.data?.chat_id ?? "" };
  }

  // 6. 新消息：使用 message.create
  const target = normalizeFeishuTarget(to);
  const receiveIdType = resolveReceiveIdType(target);
  const response = await client.im.message.create({
    params: { receive_id_type: receiveIdType },
    data: { receive_id: target, msg_type: "post", content: contentPayload },
  });
  return { messageId: response?.data?.message_id ?? "", chatId: response?.data?.chat_id ?? "" };
}
```

#### 3.2.2 卡片消息与编辑：sendCardFeishu / updateCardFeishu / editMessageFeishu

- `sendCardFeishu`：
  - 构建 `msg_type="interactive"` 的 Feishu card 消息；
  - 支持 reply 模式（`im.message.reply`）和新消息模式（`im.message.create`）。
- `buildMarkdownCard` / `sendMarkdownCardFeishu`：
  - 将 markdown 文本包装为简单卡片（schema 2.0 + markdown element）；
  - `sendMarkdownCardFeishu` 支持在卡片内做 mention。
- `editMessageFeishu`：
  - 用 `im.message.update` 更新 `post` 消息的内容；
  - 同样做 markdown 优化后构建 Feishu post payload。

这些底层函数是 Feishu 通道所有出站行为的基础。

---

## 4. 多账号配置与账号分发：core/accounts.js

文件：`src/core/accounts.js`

目标：支持一个 OpenClaw 配置中管理多个 Feishu/Lark 账号（如 prod + uat），并对每个账号做独立的 `appId`/`appSecret`、域名、启用状态等配置。

### 4.1 配置结构

注释说明：

- 顶层配置：`cfg.channels.feishu`
- 多账号：`cfg.channels.feishu.accounts[accountId]` 为覆盖字段
- 未配置 `accounts` 时使用默认账号 `DEFAULT_ACCOUNT_ID`

### 4.2 核心函数

- `getLarkConfig(cfg)`：取出 `cfg.channels.feishu` 片段；
- `getLarkAccountIds(cfg)`：
  - 返回所有显式定义的 accountId；
  - 若无，则返回 `[DEFAULT_ACCOUNT_ID]`。
- `getDefaultLarkAccountId(cfg)`：第一个账号 ID。
- `getLarkAccount(cfg, accountId)`：
  - 合并顶层配置与账号级 override；
  - 计算：
    - `configured`：是否有 `appId` + `appSecret`；
    - `enabled`：`enabled` 字段若未显式指定，则等于 `configured`；
    - `brand`：根据 `extra.domain` 或 `domain` 生成（如 `https://open.feishu.cn`）。
  - 返回对象包含：`accountId/enabled/configured/appId/appSecret/encryptKey/verificationToken/brand/config/extra`。
- `getEnabledLarkAccounts(cfg)`：过滤出既 `enabled` 又 `configured` 的账号列表。
- `getLarkCredentials(feishuCfg)`：从某个 Feishu 配置片段中提取 `appId/appSecret/encryptKey/verificationToken/brand`。
- `isConfigured(account)`：类型守卫，判断 `configured=true`。

**作用：**

- 提供统一的账号解析层，供：
  - 入站监控（每个账号一个 webhook/App）；
  - 消息发送（`LarkClient.fromCfg(cfg, accountId)`）；
  - OAuth 工具（根据消息上下文中的 `accountId` 选择正确账号）。

---

## 5. OAuth 工具与授权卡片：tools/oauth.js

文件：`src/tools/oauth.js`

该文件实现 `feishu_oauth` 工具（以及共享的 `executeAuthorize` 逻辑），用于管理用户 OAuth 授权（UAT token）：

- **工具动作**：
  - `revoke`：撤销当前用户授权；
  - `authorize` / `status` 逻辑保留在代码中，但入口已被注释，授权流程由系统自动触发（auto-auth）。

### 5.1 工具注册：registerFeishuOAuthTool

```ts
import { Type } from "@sinclair/typebox";
import { getLarkAccount } from "../core/accounts.js";
import { LarkClient } from "../core/lark-client.js";
import { getTraceContext, trace } from "../core/trace.js";
import { revokeUAT } from "../core/uat-client.js";

const FeishuOAuthSchema = Type.Object({
  action: Type.Union([ Type.Literal("revoke") ], { description: "revoke: 撤销当前用户的授权" }),
}, { description: "飞书用户授权管理工具..." });

export function registerFeishuOAuthTool(api) {
  if (!api.config) return;
  const cfg = api.config;

  api.registerTool({
    name: "feishu_oauth",
    label: "Feishu OAuth",
    description: "飞书用户授权（OAuth）管理工具...（仅 revoke）",
    parameters: FeishuOAuthSchema,
    async execute(_toolCallId, params) {
      const traceCtx = getTraceContext();
      const senderOpenId = traceCtx?.senderOpenId;
      if (!senderOpenId) {
        return json({ error: "无法获取当前用户身份（senderOpenId），请在飞书对话中使用此工具。" });
      }

      // 基于 TraceContext 中的 accountId 解析正确账号
      const acct = getLarkAccount(cfg, traceCtx.accountId);
      if (!acct.configured) {
        return json({ error: `账号 ${traceCtx.accountId} 缺少 appId 或 appSecret 配置` });
      }
      const account = acct;

      try {
        switch (params.action) {
          case "revoke": {
            await revokeUAT(account.appId, senderOpenId);
            return json({ success: true, message: "用户授权已撤销。" });
          }
          default:
            return json({ error: `未知操作: ${params.action}` });
        }
      } catch (err) {
        trace.error(`oauth: ${params.action} failed: ${err}`);
        return json({ error: formatLarkError(err) });
      }
    },
  }, { name: "feishu_oauth" });
}
```

**特性：**

- 工具不接受 `user_open_id` 参数，目标用户始终从 `TraceContext` 中推导（即“当前发消息用户”），避免 AI 伪造身份；
- 返回结果是结构化 JSON，token 的具体值不会被返回给 AI（仅状态信息）。

### 5.2 授权流程：executeAuthorize

`executeAuthorize` 是一个更完整的 Device Flow 授权流程封装，供 `feishu_oauth` 和 `feishu_oauth_batch_auth` 共享：

核心步骤：

1. **权限检查与 app owner 校验**
   - 只有应用 owner 才能发起授权流程（通过 `getAppOwnerFallback` 判断）。
2. **已有授权与 scope 覆盖检查**
   - 如果用户已有 UAT 且 scope 覆盖本次请求的 scope，则直接返回“已授权”。
3. **避免重复授权流**
   - 使用 `pendingFlows` map 追踪 `(appId, senderOpenId)` 级别的 in-flight 授权；
   - 同一条消息二次触发会合并 scope 并复用卡片；新消息触发会把旧卡片标记为“授权未完成”并新建卡片。
4. **预检查 App scope**
   - 调用 `getAppGrantedScopes` 检查应用是否已开通所需 user scope；
   - 若全部未开通，则直接返回错误提示并附带开放平台权限管理地址；
   - 若部分未开通，则过滤掉未开通 scope，仅对已开通的部分发起授权，并在卡片及返回信息中给出提示。
5. **发起 Device Flow**
   - 调用 `requestDeviceAuthorization` 获取 `verificationUriComplete`、`deviceCode` 等；
   - 构建授权卡片（`buildAuthCard`），用 Feishu 卡片形式发给用户（通过 `createCardEntity` + `sendCardByCardId`）。
6. **后台轮询**
   - 使用 `pollDeviceToken` 后台轮询用户是否完成授权；
   - 成功时：
     - 调用 `setStoredToken` 持久化 UAT；
     - 更新卡片为“授权成功”（`buildAuthSuccessCard`）；
     - 删除 `pendingFlows`；
     - 在非 onboarding 场景下，发送一条“我已完成授权”的合成消息进入 OpenClaw 的 Feishu 入站管线，触发 AI 自动重试之前的操作。
   - 失败时：
     - 更新卡片为“授权未完成”（`buildAuthFailedCard`）；
     - 删除 `pendingFlows`。

**整体来看，OAuth 工具是一个**深度集成 Feishu 卡片、OpenClaw 事件流和任务队列**的复杂模块，用于安全地管理用户授权状态，并对 AI 层“透明化”处理绝大多数授权细节。

---

## 6. 小结：Feishu 插件在 OpenClaw 中的角色

综合上述模块，Feishu OpenClaw 插件承担了以下职责：

- **通道适配层**：
  - 通过 `feishuPlugin` 将 Feishu/Lark 的事件、消息、卡片、线程模型适配到 OpenClaw 的通道抽象；
  - 使用 `feishuOutbound` 提供统一的出站接口（文本、媒体、卡片、带 channelData 的 payload）。

- **账号与配置管理**：
  - 通过 `core/accounts.js` 支持多账号（prod/uat 等）配置与路由；
  - 提供便捷的账号查询/credential 提取接口，供入站、出站和 OAuth 工具共享。

- **工具系统集成**：
  - 注册 OAPI 工具（任务、日历等），MCP Doc 工具（文档/知识库），以及安全的 OAuth 工具；
  - 对工具调用过程打点（`before_tool_call` / `after_tool_call`），形成可诊断的 trace。

- **诊断与运维友好性**：
  - 暴露 `feishu-diagnose` CLI 命令，结合 `traceByMessageId`、`analyzeTrace` 等能力对通道链路做端到端诊断；
  - 提供丰富的 trace 日志与卡片 UI，以便开发者与运维快速定位问题。

对 MW4Agent 来说，这个插件提供了一个成熟的 Feishu 通道设计范本，后续在 Python 侧实现 Feishu 通道时，可以：

- 参考 `feishuOutbound` 的分层方式（adapter + 底层 SDK 调用）；
- 参考 `accounts.js` 的多账号配置管理模型；
- 参考 `oauth.js` 在“安全性 + 用户体验 + AI 自动化”之间做平衡的授权流程设计。

