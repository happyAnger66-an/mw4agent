## OpenClaw Tools 执行权限控制原理与实现

本文基于 `openclaw` 仓库源码及本仓库现有分析文档，聚焦说明 **OpenClaw 在执行 tools 时的权限控制模型与实现细节**，以及和命令授权、沙箱、子智能体等机制之间的关系。  
配套更完整的架构分析可参考 `docs/architecture/agents/openclaw-permission-control.md`。

---

## 1. 总体安全模型：谁“能用哪些工具”

在 OpenClaw 中，**“是否调用某个工具”由 LLM 决定，** 但 **“LLM 能看到 / 能实际调用哪些工具”由权限系统决定**。

- **LLM 决策层**：  
  - 在每轮推理时，LLM 根据系统提示、工具列表和对话历史，决定是否发起 `tool_calls`（调用哪个 tool、传什么参数）。  
  - 它只能在 **当轮被“暴露”给它的工具集合** 中做选择。

- **权限控制层**：  
  - 在构建本次 run 的工具集合时，OpenClaw 会根据当前会话身份、配置和上下文，**对工具集合做多层过滤与包装**：  
    - 命令 / 消息发送者身份与是否为所有者（owner）；  
    - 工具策略（全局 / Provider / Agent / Group / Sandbox / Subagent 等）；  
    - owner-only 工具保护；  
    - 执行前的交互式批准（execution approval）。  
  - 这些策略 **决定了哪些工具会被注册给 LLM**，以及某些工具在被调用时是否会立即拒绝 / 提示批准。

换句话说：**LLM 只能在“已通过权限检查后允许暴露的工具集合”中作选择**；即使 LLM 生成了对某个被禁止工具的调用，也会在执行层被拒绝。

---

## 2. 身份与命令授权：确定“谁是 owner、谁能下命令”

tools 执行权限的基础是 **消息/命令发送者的身份与授权情况**。

- **命令授权主逻辑**：`src/auto-reply/command-auth.ts`  
  关键函数：`resolveCommandAuthorization`。

- **核心检查步骤**（简化版，完整流程详见 `openclaw-permission-control.md`）：  
  1. **通道允许列表 (`allowFrom`)**  
     - 从通道配置中获取 `allowFrom`；为空或含 `"*"` 表示允许所有发送者。  
  2. **命令专用允许列表 (`commands.allowFrom`)**  
     - 若配置则优先使用；否则回落到通道 `allowFrom` + 所有者检查。  
  3. **所有者列表 (`commands.ownerAllowFrom` + 运行时 OwnerAllowFrom)**  
     - 匹配发送者候选身份（`senderId` / `senderE164` / `from` 等标准化后的标识）。  
  4. **得出两个关键布尔值**：  
     - `senderIsOwner`: 当前发送者是否被视为 owner；  
     - `isAuthorizedSender`: 当前发送者是否被授权执行命令。

在 tools 相关逻辑中，这两个结果会被传入 **tool policy** 与 **owner-only 包装**，成为后续过滤和保护的基础。

- **类型示例**（`src/auto-reply/command-auth.ts`）：

```text
type CommandAuthorization = {
  senderIsOwner: boolean;
  isAuthorizedSender: boolean;
  senderId?: string;
  // ...
};
```

---

## 3. Owner-Only 工具：只允许所有者使用的高危工具

部分工具被显式标记为 **仅允许 Owner 使用**，通过工具元数据中的 `ownerOnly: true` 表示。例如：

- `gateway`：Gateway 配置与管理；
- `cron`：定时任务管理；
- `whatsapp_login`：WhatsApp 登录等。

### 3.1 实现入口：`applyOwnerOnlyToolPolicy`

- 代码位置：`src/agents/tool-policy.ts`
- 主要逻辑：
  - 遍历所有工具，检查是否为 owner-only；  
  - 对 owner-only 工具进行 **执行包装**：  
    - 若当前 `senderIsOwner === true`，则保留工具原始实现；  
    - 若为非 owner，则：
      - 工具会被从暴露给 LLM 的列表中过滤掉；  
      - 同时额外通过包装保证：即使被错误暴露或直接调用，也会抛出 `"Tool restricted to owner senders."` 的错误。

整体策略：

- **对所有者**：  
  - owner-only 工具仍然可用（但仍会受到后续策略管道和执行批准约束）。  
- **对非所有者**：  
  - 从工具列表中过滤掉所有 owner-only 工具 → LLM 看不到这些工具；  
  - 同时保底包装，防止异常路径绕过过滤。

---

## 4. 工具策略管道：按配置裁剪可用工具集合

在 owner-only 过滤之后，OpenClaw 通过 **工具策略管道（Tool Policy Pipeline）** 进一步对工具列表做 **按配置裁剪**。

### 4.1 策略层级

策略分层在 `docs/architecture/agents/openclaw-permission-control.md` 中已有详细说明，这里聚焦 tools 视角。  
大致顺序（从通用到具体）：

1. **Profile 策略**（`tools.profile`）  
   - 预定义工具集，如：
     - `minimal`：仅基础状态类工具（如 `session_status`）；  
     - `coding`：文件系统 / 运行时 / 会话 / 内存相关工具；  
     - `messaging`：仅消息发送与路由工具；  
     - `full`：近乎不限制的完整工具集（不再通过 profile 过滤工具）。

> 注意：在 OpenClaw 中（以及本仓库对齐实现中），`tools.profile` 只控制“暴露给 LLM 的工具集合”。
> 文件系统工具（`read` / `write` / `edit` / `apply_patch`）是否限制在 workspace 目录内由 `tools.fs.workspaceOnly` 控制，
> 与 `profile: full` 无直接绑定关系。
2. **Provider Profile 策略**（`tools.byProvider.profile`）  
   - 按 LLM provider（OpenAI / Anthropic / xAI 等）进一步约束，例如对带 web 能力的模型禁用 OpenClaw 自带 `web_search`。  
3. **全局 allow/deny**（`tools.allow` / `tools.deny`）  
4. **Provider 级策略**（`tools.byProvider.allow` / `tools.byProvider.deny`）  
5. **Agent 级策略**（`agents.{agentId}.tools.allow` / `.deny`）  
6. **Group / Channel 级策略**（消息通道共享的一组工具策略）  
7. **Sandbox 策略**（沙箱环境专用）  
8. **Subagent 策略**（子智能体专用）

### 4.2 实现入口：`applyToolPolicyPipeline`

- 代码位置：`src/agents/tool-policy-pipeline.ts`
- 核心函数：`applyToolPolicyPipeline(params)`：
  - 入参包含：
    - `tools`: 当前候选工具列表；  
    - `toolMeta`: 提供工具来源（pluginId 等）信息；  
    - `steps`: 按顺序要应用的策略步骤列表。
  - 实现思路：
    - 依次遍历策略步骤，若存在策略配置，则：  
      - 展开插件组、工具组等（`expandPolicyWithPluginGroups`）；  
      - 调用 `filterToolsByPolicy` 根据 allow/deny 列表过滤工具。

### 4.3 策略匹配规则

通用匹配规则如下：

- **`deny` 优先于 `allow`**：一旦匹配 `deny`，工具即被移除。  
- 支持通配符 `*`（匹配所有工具）。  
- 支持 **工具组**（如 `group:fs`、`group:runtime`）与 **插件组**（如 `group:plugins`），方便大范围一键禁用。  
- 不配置 `allow` 时，默认为“只看 deny”；若配置了 `allow`，则按 allow 作为白名单基础。

### 4.4 典型配置示例

（节选自 `openclaw-permission-control.md`）

```text
{
  tools: {
    profile: "coding",
    allow: ["slack", "discord"],
    deny: ["exec", "process"],
  },
  tools: {
    byProvider: {
      "openai": {
        profile: "messaging",
        deny: ["browser"],
      },
    },
  },
  agents: {
    list: [
      {
        id: "support",
        tools: {
          profile: "messaging",
          allow: ["slack"],
        },
      },
    ],
  },
}
```

对 tools 执行权限的影响是：**只有最终通过所有策略步骤筛选的工具才会暴露给 LLM 并有机会被执行。**

---

## 5. 沙箱与子智能体的工具权限

### 5.1 沙箱工具策略（Sandbox Tool Policy）

- 代码位置：`src/agents/sandbox/tool-policy.ts`
- 关键函数：`isToolAllowed(policy, name)`：
  - 先编译 `policy.deny` 中的 glob 模式；若匹配则直接拒绝；  
  - 再编译 `policy.allow`：  
    - 若 `allow` 为空，则表示“未显式限制 → 允许所有未被 deny 的工具”；  
    - 若 `allow` 非空，则必须匹配 allow 中的某个模式才被允许。

其作用是为 **沙箱环境提供独立的工具白/黑名单**，通常用于：

- 在沙箱中强制禁用 `exec` / `process` 等系统级工具；  
- 限制只允许只读类工具（如 `read`、`web_search`）。

### 5.2 子智能体工具策略（Subagent Tool Policy）

- 代码位置：`src/agents/pi-tools.policy.ts`
- 关键函数：`resolveSubagentToolPolicy(cfg, depth)`：
  - 根据当前子智能体的 **递归深度 `depth`** 与配置中的 `maxSpawnDepth` 计算默认 deny 列表；  
  - 若 `depth >= maxSpawnDepth`，直接返回 `{ deny: ["*"] }`，禁止所有工具；  
  - 否则组合：
    - 默认 deny（随深度增加而更严格）；  
    - 配置中的 `cfg.agents.defaults.subagents.tools.allow/deny`。

意义在于：**越“深层”的子智能体，可操作的工具越少，甚至完全禁止，防止无限自我扩展和越权操作。**

---

## 6. 执行批准（Execution Approval）：对危险工具的交互式二次确认

对于高危操作（特别是 `exec` 这类能执行系统命令的工具），即使通过了 owner-only 和工具策略过滤，OpenClaw 仍会在 **真正执行前要求额外批准**。

### 6.1 节点执行中的批准流程

- 主要场景：通过 `nodes` 工具在远程节点上执行命令。  
- 代码位置：`src/agents/tools/nodes-tool.ts`。

核心流程简述：

1. 在准备好命令与执行计划后，调用 Gateway 工具：`exec.approval.request`：  
   - 包含命令文本、参数、工作目录、节点 ID、超时等；  
2. 等待 Gateway / 前端（如桌面 App）给出批准结果；  
3. 若返回的 `decision` 不为 `allow-once` 或 `allow-always`，则抛出 `exec denied: user denied` 错误。

批准选项通常包括：

- `allow-once`：本次执行允许，之后仍需询问；  
- `allow-always`：记住本次模式，对相同范围的操作自动批准；  
- `reject-once` / `reject-always`：本次 / 后续拒绝。

### 6.2 ACP 客户端侧批准逻辑

- 代码位置：`src/acp/client.ts`  
- 核心函数：`resolvePermissionRequest`：
  - 根据工具名、标题、当前工作目录等信息，判断是否可以自动批准：  
    - 对低风险工具，如只读查询类工具，可直接 auto-approve；  
    - 对在 `DANGEROUS_ACP_TOOLS` 集合中的高危工具，**始终要求人工确认**。  
  - 若需要提示，则通过 UI 向用户展示工具调用信息（包括命令、路径等），等待用户确认；  
  - 用户若同意，则返回 allow option，对应本地策略更新 / 允许执行；  
  - 用户拒绝或取消，则返回拒绝结果，tools 执行层会以错误形式中止调用。

这一步保证了：**即便配置和策略允许工具被暴露，真正危险的执行仍需最终用户做交互式确认。**

---

## 7. 端到端：一次 tools 调用的权限检查序列

结合上述各层，tools 执行权限的大致序列可以归纳为：

1. **消息接收与身份解析**  
   - 解析发送者身份，运行 `resolveCommandAuthorization` 得到 `senderIsOwner`、`isAuthorizedSender`。  
2. **命令 / 会话创建是否允许**  
   - 若发送者未被授权，命令层直接拒绝，不进入 Agent / tools 运行。  
3. **构建工具集合**  
   - 通过 `createOpenClawCodingTools` 等函数组装原始 tools 列表。  
4. **Owner-only 工具处理**  
   - 使用 `applyOwnerOnlyToolPolicy`：对 owner-only 工具做包装，并在非 owner 情况下从列表中过滤。  
5. **工具策略管道裁剪**  
   - 通过 `applyToolPolicyPipeline` 应用全局 / Provider / Agent / Group / Sandbox / Subagent 等多层策略，最终形成本轮可用工具集合。  
6. **将可用工具集合注册给 LLM**  
   - 仅这些工具出现在当前会话中，LLM 才有能力发起调用。  
7. **LLM 选择并发起 tool_calls**  
   - 若模型决定调用某个工具，则 run loop 根据 name 找到对应实现并准备执行。  
8. **（可选）执行批准**  
   - 对于标记为高危或需要批准的工具（如 `exec`），在真正执行前通过 ACP / Gateway 请求用户确认：  
     - 未获批准则中止执行并返回错误；  
9. **实际执行与结果写回**  
   - 工具执行成功后，将结果写回 transcript，再由 LLM 继续推理下一步。

---

## 8. 常用安全配置建议（面向 tools）

结合上面的机制，实践中可以通过配置实现“默认安全、按需开放”的策略：

- **限制命令与 owner 身份**  
  - 在配置中显式设置 `commands.allowFrom` 与 `commands.ownerAllowFrom`，将 owner 限定为少数可信身份。  
- **合理选择 tools profile**  
  - 对大多数会话使用 `profile: "messaging"` 或 `profile: "minimal"`，只为需要代码/节点访问的场景开启 `coding` 或更高权限。  
- **显式 deny 高危工具**  
  - 如 `exec`、`process`、`gateway`、`cron`、`sessions_spawn` 等，即便是 owner 会话也建议默认 deny，需要时临时放开。  
- **对子智能体设置严格默认策略**  
  - 使用较小的 `maxSpawnDepth`，并在 `agents.defaults.subagents.tools.deny` 中加入 `"*"` 或重要危险工具名，防止子智能体链式放大权限。  
- **对沙箱会话单独设策略**  
  - 在沙箱中只开放读、查询类工具，禁用写文件、执行命令和外部系统访问。

---

## 9. 相关源码索引（openclaw 仓库）

- **命令授权与身份解析**：`src/auto-reply/command-auth.ts`  
- **Owner-only 工具与基础工具策略**：`src/agents/tool-policy.ts`  
- **工具策略管道实现**：`src/agents/tool-policy-pipeline.ts`  
- **沙箱工具策略**：`src/agents/sandbox/tool-policy.ts`  
- **子智能体工具策略**：`src/agents/pi-tools.policy.ts`  
- **节点执行与执行批准**：`src/agents/tools/nodes-tool.ts`  
- **ACP 客户端审批逻辑**：`src/acp/client.ts`  
- **工具与系统提示整体组装（含 tools 列表构建）**：  
  - `src/agents/pi-tools.ts`（`createOpenClawCodingTools`）  
  - `src/agents/pi-embedded-runner/run/attempt.ts`（run 上下文与工具集组装）

上述索引可配合本仓库的 `docs/architecture/agents/openclaw-permission-control.md` 一起阅读，以获得对 OpenClaw tools 执行权限控制的完整认识。

