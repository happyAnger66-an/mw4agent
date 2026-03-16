## OpenClaw Sandbox 执行原理与流程

本文说明 **OpenClaw 中 sandbox（沙箱）执行的整体设计、关键策略与端到端执行流程**，作为 `docs/openclaw/auth.md` 的配套文档，侧重解释“在沙箱中 tools 是如何被限制和执行的”。

---

## 1. Sandbox 的定位：在普通权限体系上的一层“局部安全罩”

在 OpenClaw 中：

- **普通 run**：  
  - 只受身份 / owner-only / 工具策略管道 / 子智能体策略 / 执行批准影响；
  - LLM 能使用的 tools 由这几层共同决定。

- **Sandbox run**：  
  - 在以上所有层之上，再叠加一层 **Sandbox 专用的工具策略**；
  - 只在 **当前这类 run / 会话** 内生效，对其它会话不干扰；
  - 典型用途：
    - 只读代码审查（不能写文件、不能 exec）；
    - 灰度开放新工具（先在 sandbox 限流测试）；
    - 对高危任务提供“试验环境”。

可以将 sandbox 理解为：

> 在“已经过全局权限控制的 tools 集合”之上，再做一次较小范围的二次筛选与执行限制。

---

## 2. Sandbox 工具策略模型：`SandboxToolPolicy`

Sandbox 中对 tools 的限制通过一个独立的策略结构完成：`SandboxToolPolicy`，核心字段通常包括：

- **`allow: string[] | undefined`**  
  - 一组 glob / 组名形式的工具匹配模式，表示白名单；
  - 示例：`["read", "web_search", "group:fs-readonly"]`。

- **`deny: string[] | undefined`**  
  - 一组 glob / 组名形式的工具匹配模式，表示黑名单；
  - 示例：`["exec", "process", "group:runtime", "*_write"]`。

**匹配规则**由 `src/agents/sandbox/tool-policy.ts` 中的 `isToolAllowed` 实现：

```text
export function isToolAllowed(
  policy: SandboxToolPolicy,
  name: string
): boolean {
  const normalized = normalizeGlob(name);

  // 1. deny 优先：命中即拒绝
  const deny = compileGlobPatterns({
    raw: expandToolGroups(policy.deny ?? []),
    normalize: normalizeGlob,
  });
  if (matchesAnyGlobPattern(normalized, deny)) {
    return false;
  }

  // 2. allow 控制白名单范围
  const allow = compileGlobPatterns({
    raw: expandToolGroups(policy.allow ?? []),
    normalize: normalizeGlob,
  });

  // allow 为空：代表“只看 deny”，其它都允许
  if (allow.length === 0) {
    return true;
  }

  // allow 非空：代表“白名单模式”，必须匹配 allow 才允许
  return matchesAnyGlobPattern(normalized, allow);
}
```

简化总结：

- **deny 优先级最高**：匹配到 `deny` 的工具一定被禁止；
- **allow 控制模式**：
  - `allow` 为空 → 只受 `deny` 约束，类似“黑名单模式”；
  - `allow` 非空 → 必须匹配 `allow`，类似“白名单模式”；
- 支持：
  - glob 模式（如 `*`、`*_write`）；
  - 工具组（如 `group:fs`、`group:runtime`），通过 `expandToolGroups` 展开。

---

## 3. Sandbox 与常规权限体系的关系

在 `docs/openclaw/auth.md` 中，整体权限管线大致为：

```text
消息接收
  ↓
命令授权 / 身份解析（senderIsOwner, isAuthorizedSender）
  ↓
Owner-only 工具策略（gateway/cron 等）
  ↓
通用工具策略管道（全局 / Provider / Agent / Group）
  ↓
Sandbox 策略（本节）
  ↓
Subagent 策略（按深度约束）
  ↓
执行批准（exec 等危险操作二次确认）
  ↓
工具实际执行
```

其中 **Sandbox 所在位置**：

- 它不是替代现有策略，而是作为 **中间一层“局部收口”**：
  - 先用通用策略把“系统级可用工具集合”确定下来；
  - 再用 Sandbox 策略为本次 run 做更严格的裁剪；
  - 然后再叠加 Subagent / 执行批准等后续约束。

因此：

- Sandbox 必须在 **上游命令授权通过** 且 **基础工具已被允许** 的前提下才有意义；
- 它负责把 **已经合法的那一批工具** 再做一次“环境级限制”，而不是解锁本来就被全局 deny 的工具。

---

## 4. Sandbox 执行端到端流程

从“一次在 Sandbox 中运行 agent”的视角看，执行流程如下：

### 4.1 入口与上下文构建

1. **入口标记 Sandbox**  
   - 上游（CLI、桌面 UI、Web UI、Gateway 等）发起一次 run 时，可以通过参数或配置指定：  
     - 本次 run 使用 sandbox；  
     - 或某个 channel / agent 默认在 sandbox 模式下运行。

2. **构建 run 上下文**  
   - 和普通 run 一样：  
     - 解析 session / workspace / provider / 配置；  
     - 解析 skills 与 system prompt；  
     - 解析命令授权，得到 `senderIsOwner` / `isAuthorizedSender`。

### 4.2 工具集合构建与 Sandbox 过滤

3. **构建初始工具集合**  
   - 通过 `createOpenClawCodingTools(...)` 等函数生成一批“原始工具”：
     - 文件系统相关：`read`、`write`、`apply_patch` 等；
     - 节点 / Gateway：`nodes`、`gateway`、`cron` 等；
     - 会话 / 内存 / web / 浏览器等工具。

4. **应用通用权限控制**（参考 `auth.md`）  
   - Owner-only 工具保护（`applyOwnerOnlyToolPolicy`）；  
   - 工具策略管道（`applyToolPolicyPipeline`）：  
     - 全局 / Provider / Agent / Group 等策略；  
   - 这一阶段产出的是 **系统级可用工具集合**。

5. **应用 Sandbox 工具策略**  
   - 若本次 run 标记为 sandbox，则加载相应的 `SandboxToolPolicy`（例如按 run 类型 / agent / 入口配置决定）；  
   - 对上一步得到的工具集合逐个调用 `isToolAllowed(policy, tool.name)`：  
     - `false` → 从集合中移除；  
     - `true` → 保留。  
   - 得到 **“Sandbox 内可用工具集合”**。

6. **将 Sandbox 过滤后的工具暴露给 LLM**  
   - 作为当前会话的 `tools` 列表传给 Provider；  
   - LLM 在这个会话里只“看得见”这些工具，无法选择其他工具。

### 4.3 tool_calls、执行与返回

7. **LLM 决定是否调用工具**  
   - 根据系统提示（含技能 / 工具摘要）和用户消息，LLM 决定是否输出 `tool_calls`；  
   - 若调用某个工具，run loop 根据 name 在“Sandbox 内可用工具集合”中查找实现。

8. **执行前再检查 Sandbox 策略（可选的收口）**  
   - 某些实现会在 execute 前再次调用 `isToolAllowed(policy, tool.name)` 做保底校验：  
     - 防止因配置错误等导致的越权工具执行。  

9. **执行批准（危险工具）**  
   - 对于 `exec` 等高危工具，即便通过了 Sandbox，也会进入执行批准流程（`nodes-tool.ts` + `acp/client.ts`）；  
   - 用户在 UI 中看到工具详情，选择 allow / reject；  
   - 未获批准则报错并中止本次调用。

10. **工具执行与结果写回**  
    - 工具成功执行后，将结果写入 transcript；  
    - 再带着新的历史继续请求 LLM，直到模型不再返回 `tool_calls`。

---

## 5. Sandbox 常见使用模式与配置思路

从实践角度，可以通过 Sandbox 实现几类典型安全模式：

### 5.1 只读代码审查 Sandbox

- 目标：让 LLM 只读代码 / 文档，给出建议，但不能改动文件或执行命令。
- 策略示例（逻辑上）：

```text
SandboxToolPolicy {
  allow: ["read", "web_search", "memory_*", "session_status"],
  deny: ["write", "apply_patch", "exec", "process", "gateway", "cron"],
}
```

效果：

- 在 sandbox run 中，模型只能：
  - 读取文件、查 web、查记忆等；  
  - 不会真正写代码或跑命令。

### 5.2 有执行批准的实验 Sandbox

- 目标：允许 LLM 在 sandbox 中计划并尝试执行命令，但每次执行必须得到人工批准。
- 策略组合：

1. Sandbox 层：
   - 允许 `exec` 相关工具，但禁止某些明显高危工具组（如 `group:runtime-root`）。  
2. 执行批准层（ACP）：
   - 将 `exec` 工具加入 `DANGEROUS_ACP_TOOLS` 集合，强制弹出确认。  

效果：

- LLM 可以在 sandbox 内完整规划“下载依赖 / 跑测试”等命令；  
- 每一个 `exec` 调用都会在 UI 中提示用户确认，用户可以单次允许或拒绝。

### 5.3 子智能体 + Sandbox 的双重限制

- 目标：避免深层子智能体拥有过大的执行能力。
- 组合方式：

1. Subagent 策略（`resolveSubagentToolPolicy`）：  
   - 随深度增加逐步扩展 `deny` 列表，甚至在超过 `maxSpawnDepth` 后 `deny: ["*"]`。  
2. Sandbox 策略：  
   - 对特定子智能体类型强制附加只读 / 禁 exec 的 SandboxPolicy。  

效果：

- 顶层 agent 可以拥有一定执行能力；  
- 深层 subagent 绝大多数时候只能分析和规划，无法真正改动系统。

---

## 6. 与 `auth.md` 的关系与阅读建议

从视角上看：

- `docs/openclaw/auth.md`：  
  - 站在 **整体 tools 权限体系** 的角度，说明身份 / owner-only / 工具策略管道 / Sandbox / Subagent / 执行批准等所有层级；
  - 重点答“谁能执行哪些 tools、整体如何防越权”。

- `docs/openclaw/sandbox.md`（本文）：  
  - 聚焦在 **Sandbox 这一层**，说明它与其它层的关系，以及在一次 run 中如何影响工具的暴露与执行；
  - 更适合从“我要用沙箱跑这个 Agent，系统究竟多了一些什么保护”来理解。

推荐阅读顺序：

1. 先读 `auth.md` 理解整体权限模型；  
2. 再读本 `sandbox.md`，理解在具体 run 级别上，Sandbox 是如何叠加在工具集合与执行流程上的。

