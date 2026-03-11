# OpenClaw 执行命令权限控制分析

本文档分析 OpenClaw 中执行命令和工具调用的权限控制机制。

## 1. 权限控制架构概览

OpenClaw 采用**多层权限控制**机制，遵循"身份优先、范围其次、模型最后"的安全原则：

```
身份验证 (Identity)
  ↓
范围控制 (Scope)
  ↓
工具策略 (Tool Policy)
  ↓
所有者权限 (Owner Permission)
  ↓
执行批准 (Execution Approval)
```

## 2. 命令授权 (Command Authorization)

### 2.1 授权流程

命令授权在 `src/auto-reply/command-auth.ts` 中实现，主要函数是 `resolveCommandAuthorization`。

**授权检查层次：**

1. **通道允许列表 (Channel Allowlist)**
   - 从通道配置中获取 `allowFrom` 列表
   - 如果列表为空或包含 `"*"`，则允许所有发送者

2. **命令专用允许列表 (commands.allowFrom)**
   - 如果配置了 `commands.allowFrom`，优先使用此列表
   - 否则回退到通道 `allowFrom` + 所有者检查

3. **所有者列表 (Owner List)**
   - 从配置中解析 `commands.ownerAllowFrom`
   - 从上下文解析 `OwnerAllowFrom`
   - 匹配发送者身份

4. **发送者身份解析**
   - 从消息上下文中提取 `senderId`、`senderE164`、`from` 等
   - 标准化身份标识符

### 2.2 关键代码

```typescript
// src/auto-reply/command-auth.ts

export function resolveCommandAuthorization(params: {
  ctx: MsgContext;
  cfg: OpenClawConfig;
  commandAuthorized: boolean;
}): CommandAuthorization {
  // 1. 解析命令专用允许列表
  const commandsAllowFromList = resolveCommandsAllowFromList({...});
  
  // 2. 解析通道允许列表
  const allowFromList = formatAllowFromList({...});
  
  // 3. 解析所有者列表
  const ownerList = resolveOwnerAllowFromList({...});
  
  // 4. 解析发送者候选
  const senderCandidates = resolveSenderCandidates({...});
  
  // 5. 匹配发送者
  const matchedSender = ownerList.length
    ? senderCandidates.find(candidate => ownerList.includes(candidate))
    : undefined;
  
  // 6. 判断是否为所有者
  const senderIsOwner = Boolean(matchedSender);
  
  // 7. 判断是否授权
  const isAuthorizedSender = commandsAllowFromList !== null
    ? commandsAllowAll || Boolean(matchedCommandsAllowFrom)
    : commandAuthorized && isOwnerForCommands;
  
  return {
    senderIsOwner,
    isAuthorizedSender,
    // ...
  };
}
```

### 2.3 授权结果

```typescript
type CommandAuthorization = {
  senderIsOwner: boolean;        // 是否为所有者
  isAuthorizedSender: boolean;    // 是否授权执行命令
  senderId?: string;              // 发送者 ID
  // ...
};
```

## 3. 工具权限控制 (Tool Permission Control)

### 3.1 所有者专用工具 (Owner-Only Tools)

某些工具被标记为 `ownerOnly: true`，只有所有者可以执行。

**内置所有者专用工具：**
- `gateway` - Gateway 配置工具
- `cron` - 定时任务工具
- `whatsapp_login` - WhatsApp 登录工具

**实现机制：**

```typescript
// src/agents/tool-policy.ts

export function applyOwnerOnlyToolPolicy(
  tools: AnyAgentTool[],
  senderIsOwner: boolean
): AnyAgentTool[] {
  const withGuard = tools.map((tool) => {
    if (!isOwnerOnlyTool(tool)) {
      return tool;
    }
    // 包装工具执行，检查权限
    return wrapOwnerOnlyToolExecution(tool, senderIsOwner);
  });
  
  if (senderIsOwner) {
    return withGuard;  // 所有者：保留所有工具
  }
  
  // 非所有者：过滤掉所有者专用工具
  return withGuard.filter((tool) => !isOwnerOnlyTool(tool));
}

function wrapOwnerOnlyToolExecution(
  tool: AnyAgentTool,
  senderIsOwner: boolean
): AnyAgentTool {
  if (tool.ownerOnly !== true || senderIsOwner || !tool.execute) {
    return tool;
  }
  return {
    ...tool,
    execute: async () => {
      throw new Error("Tool restricted to owner senders.");
    },
  };
}
```

### 3.2 工具策略系统 (Tool Policy System)

OpenClaw 使用**策略管道 (Policy Pipeline)** 来过滤工具。

**策略层次（从高到低）：**

1. **Profile 策略** (`tools.profile`)
   - `minimal` - 仅 `session_status`
   - `coding` - 文件系统、运行时、会话、内存工具
   - `messaging` - 消息工具
   - `full` - 无限制

2. **Provider Profile 策略** (`tools.byProvider.profile`)

3. **全局策略** (`tools.allow` / `tools.deny`)

4. **Provider 策略** (`tools.byProvider.allow` / `tools.byProvider.deny`)

5. **Agent 策略** (`agents.{agentId}.tools.allow` / `agents.{agentId}.tools.deny`)

6. **Provider Agent 策略** (`agents.{agentId}.tools.byProvider.allow`)

7. **Group 策略** (通道级别的工具策略)

8. **Sandbox 策略** (沙箱环境的工具策略)

9. **Subagent 策略** (子智能体的工具策略)

**策略应用流程：**

```typescript
// src/agents/tool-policy-pipeline.ts

export function applyToolPolicyPipeline(params: {
  tools: AnyAgentTool[];
  toolMeta: (tool: AnyAgentTool) => { pluginId: string } | undefined;
  warn: (message: string) => void;
  steps: ToolPolicyPipelineStep[];
}): AnyAgentTool[] {
  let filtered = params.tools;
  
  // 按顺序应用每个策略步骤
  for (const step of params.steps) {
    if (!step.policy) {
      continue;
    }
    
    // 扩展插件组
    const expanded = expandPolicyWithPluginGroups(step.policy, pluginGroups);
    
    // 过滤工具
    filtered = expanded 
      ? filterToolsByPolicy(filtered, expanded)
      : filtered;
  }
  
  return filtered;
}
```

**策略匹配规则：**

- `deny` 优先于 `allow`
- 支持通配符 `*`（匹配所有工具）
- 支持工具组（如 `group:fs`、`group:runtime`）
- 支持插件组（如 `group:plugins`）

### 3.3 工具策略配置示例

```json5
{
  // 全局工具策略
  tools: {
    profile: "coding",              // 基础工具集
    allow: ["slack", "discord"],     // 额外允许的工具
    deny: ["exec", "process"],      // 明确拒绝的工具
  },
  
  // Provider 特定策略
  tools: {
    byProvider: {
      "openai": {
        profile: "messaging",
        deny: ["browser"],
      },
    },
  },
  
  // Agent 特定策略
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

## 4. 执行批准 (Execution Approval)

对于危险操作（如 `exec` 命令），OpenClaw 支持**交互式批准**机制。

### 4.1 批准流程

**实现位置：** `src/agents/tools/nodes-tool.ts`

```typescript
// 请求批准
const approvalResult = await callGatewayTool(
  "exec.approval.request",
  {
    id: approvalId,
    command: prepared.cmdText,
    commandArgv: prepared.plan.argv,
    systemRunPlan: prepared.plan,
    cwd: prepared.plan.cwd ?? cwd,
    nodeId,
    host: "node",
    timeoutMs: APPROVAL_TIMEOUT_MS,
  },
);

// 检查批准决定
const approvalDecision =
  decisionRaw === "allow-once" || decisionRaw === "allow-always"
    ? decisionRaw
    : null;

if (!approvalDecision) {
  throw new Error("exec denied: user denied");
}
```

**批准选项：**
- `allow-once` - 允许一次
- `allow-always` - 始终允许
- `reject-once` - 拒绝一次
- `reject-always` - 始终拒绝

### 4.2 ACP 客户端批准

**实现位置：** `src/acp/client.ts`

```typescript
export async function resolvePermissionRequest(
  params: RequestPermissionRequest,
  deps: PermissionResolverDeps = {},
): Promise<RequestPermissionResponse> {
  // 自动批准检查
  const autoApproveAllowed = shouldAutoApproveToolCall(
    params,
    toolName,
    toolTitle,
    cwd,
  );
  
  // 危险工具需要提示
  const promptRequired = !toolName || 
                         !autoApproveAllowed || 
                         DANGEROUS_ACP_TOOLS.has(toolName);
  
  if (!promptRequired) {
    // 自动批准
    return selectedPermission(allowOption.optionId);
  }
  
  // 提示用户
  const approved = await prompt(toolName, toolTitle);
  
  if (approved && allowOption) {
    return selectedPermission(allowOption.optionId);
  }
  
  return cancelledPermission();
}
```

## 5. 沙箱权限 (Sandbox Permissions)

沙箱环境有独立的工具策略系统。

**实现位置：** `src/agents/sandbox/tool-policy.ts`

```typescript
export function isToolAllowed(
  policy: SandboxToolPolicy,
  name: string
): boolean {
  const normalized = normalizeGlob(name);
  
  // deny 优先
  const deny = compileGlobPatterns({
    raw: expandToolGroups(policy.deny ?? []),
    normalize: normalizeGlob,
  });
  if (matchesAnyGlobPattern(normalized, deny)) {
    return false;
  }
  
  // allow 检查
  const allow = compileGlobPatterns({
    raw: expandToolGroups(policy.allow ?? []),
    normalize: normalizeGlob,
  });
  if (allow.length === 0) {
    return true;  // 无 allow 列表 = 允许所有
  }
  
  return matchesAnyGlobPattern(normalized, allow);
}
```

## 6. 子智能体权限 (Subagent Permissions)

子智能体有独立的工具策略，可以限制其可用的工具。

**实现位置：** `src/agents/pi-tools.policy.ts`

```typescript
export function resolveSubagentToolPolicy(
  cfg?: OpenClawConfig,
  depth?: number
): SandboxToolPolicy {
  const maxSpawnDepth = cfg?.agents?.defaults?.subagents?.maxSpawnDepth ?? 5;
  
  // 根据深度限制工具
  if (depth && depth >= maxSpawnDepth) {
    return { deny: ["*"] };  // 禁止所有工具
  }
  
  // 默认策略
  const defaultDeny = resolveSubagentDenyList(depth ?? 0, maxSpawnDepth);
  
  return {
    allow: cfg?.agents?.defaults?.subagents?.tools?.allow,
    deny: [
      ...defaultDeny,
      ...(cfg?.agents?.defaults?.subagents?.tools?.deny ?? []),
    ],
  };
}
```

## 7. 权限检查流程总结

### 7.1 命令执行权限检查

```
消息接收
  ↓
解析发送者身份
  ↓
检查通道 allowFrom
  ↓
检查 commands.allowFrom (如果配置)
  ↓
检查所有者列表
  ↓
判断 senderIsOwner
  ↓
判断 isAuthorizedSender
  ↓
执行命令（如果授权）
```

### 7.2 工具调用权限检查

```
工具调用请求
  ↓
应用所有者专用工具策略
  ↓
应用工具策略管道
  ├─ Profile 策略
  ├─ Provider Profile 策略
  ├─ 全局策略
  ├─ Provider 策略
  ├─ Agent 策略
  ├─ Group 策略
  ├─ Sandbox 策略
  └─ Subagent 策略
  ↓
检查执行批准（如需要）
  ↓
执行工具（如果授权）
```

## 8. 安全最佳实践

### 8.1 配置建议

```json5
{
  // 1. 限制命令执行
  commands: {
    allowFrom: ["owner@example.com"],  // 明确指定允许的发送者
    ownerAllowFrom: ["owner@example.com"],
  },
  
  // 2. 限制工具访问
  tools: {
    profile: "messaging",  // 使用最小权限配置
    deny: ["gateway", "cron", "sessions_spawn"],  // 拒绝危险工具
  },
  
  // 3. 限制子智能体
  agents: {
    defaults: {
      subagents: {
        maxSpawnDepth: 2,  // 限制嵌套深度
        tools: {
          deny: ["*"],  // 禁止所有工具
        },
      },
    },
  },
}
```

### 8.2 危险工具

以下工具应谨慎使用，建议默认拒绝：

- `gateway` - 可以修改配置
- `cron` - 可以创建定时任务
- `sessions_spawn` - 可以创建子智能体
- `exec` - 可以执行系统命令
- `process` - 可以管理进程

## 9. 代码位置总结

| 功能 | 文件路径 |
|------|---------|
| 命令授权 | `src/auto-reply/command-auth.ts` |
| 工具策略 | `src/agents/tool-policy.ts` |
| 工具策略管道 | `src/agents/tool-policy-pipeline.ts` |
| 所有者专用工具 | `src/agents/tool-policy.ts` |
| 沙箱工具策略 | `src/agents/sandbox/tool-policy.ts` |
| 子智能体策略 | `src/agents/pi-tools.policy.ts` |
| 执行批准 | `src/agents/tools/nodes-tool.ts` |
| ACP 批准 | `src/acp/client.ts` |

## 10. 参考文档

- [Security Guide](/gateway/security/index.md)
- [Tools Documentation](/tools/index.md)
- [Command Authorization](/auto-reply/command-auth.ts)
- [Tool Policy](/agents/tool-policy.ts)
