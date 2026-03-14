# OpenClaw 多 Node 管理与跨 Node 执行命令

本文基于 `openclaw` 仓库源码，总结 **OpenClaw 如何管理多个 node** 以及 **如何在不同 node 上执行命令**，便于在 MW4Agent 中理解或对标实现类似能力。

---

## 1. 多 Node 管理

### 1.1 概念与数据来源

- **Node**：一台设备（手机、Mac、headless 主机等），运行 OpenClaw 的 node-host 或伴侣应用，通过 WebSocket 连接 Gateway。
- 管理分为两类数据：
  - **配对信息（paired）**：持久化在 Gateway 侧（`listDevicePairing()` / `node-pairing`），包含 nodeId、displayName、platform、caps、commands 等。
  - **在线会话（connected）**：内存中的 `NodeRegistry`，仅包含当前通过 WebSocket 连上的 node。

### 1.2 NodeRegistry（在线 node 注册表）

- **位置**：`src/gateway/node-registry.ts`
- **职责**：
  - 维护 `nodesById`（nodeId → NodeSession）、`nodesByConn`（connId → nodeId）。
  - Node 通过 WebSocket 握手时，若 `role === "node"`，在 `src/gateway/server/ws-connection/message-handler.ts` 中调用 `context.nodeRegistry.register(nextClient, { remoteIp })` 完成注册。
  - 每个 NodeSession 包含：nodeId、connId、client（WS 客户端）、displayName、platform、caps、commands、pathEnv、connectedAtMs 等。
- **主要接口**：
  - `register(client, opts)`：注册一个已连接的 node。
  - `unregister(connId)`：连接断开时移除，并 reject 该 node 上所有未完成的 invoke。
  - `listConnected()`：返回当前所有在线 node。
  - `get(nodeId)`：按 nodeId 查在线会话。
  - `invoke(params)`：向指定 node 发送 `node.invoke.request` 事件并等待 `node.invoke.result`。
  - `handleInvokeResult(params)`：处理 node 回传的 invoke 结果，resolve 对应 Promise。

即：**多 node 的“在线状态”完全由 Gateway 的 NodeRegistry 管理**，配对信息则来自设备配对持久化。

### 1.3 配对（Pairing）流程

- **RPC**：`node.pair.request`、`node.pair.list`、`node.pair.approve`、`node.pair.reject`、`node.pair.verify`。
- **持久化**：`src/infra/node-pairing.ts` 使用 `pairing-files` 的 `pendingPath` / `pairedPath` 存储待审批与已配对设备。
- 配对通过后，设备拥有 nodeId 和 token；之后 node-host 或伴侣应用用该身份连接 Gateway，握手时被识别为 `role: "node"` 并注册进 NodeRegistry。

### 1.4 node.list / node.describe

- **node.list**（`src/gateway/server-methods/nodes.ts` 约 536 行起）：
  - 调用 `listDevicePairing()` 得到已配对列表，再 `context.nodeRegistry.listConnected()` 得到在线列表。
  - 合并两者：同一 nodeId 下合并 paired 与 live 的 caps/commands 等，并标记 `paired`、`connected`。
  - 返回 `{ ts, nodes }`，nodes 按“已连接优先、再按名称”排序。
- **node.describe**：按 nodeId 查单个 node 的配对 + 在线详情（caps、commands、platform 等）。

因此：**“管理多个 node” = 配对表（持久化）+ NodeRegistry（在线），通过 node.list / node.describe 对外统一视图**。

---

## 2. 在不同 Node 上执行命令

### 2.1 入口：node.invoke RPC

- **协议**：`NodeInvokeParamsSchema`（`src/gateway/protocol/schema/nodes.ts`）：`nodeId`、`command`、`params`（可选）、`timeoutMs`（可选）、`idempotencyKey`。
- **Gateway 处理**（`src/gateway/server-methods/nodes.ts` 约 776 行起）：
  1. 校验参数；禁止 `system.execApprovals.*` 通过 node.invoke 调用。
  2. **若 node 未连接**：先尝试 APNs 唤醒（`maybeWakeNodeWithApns`），再 `waitForNodeReconnect` 轮询 NodeRegistry；仍不可用则返回 `NOT_CONNECTED` 或把 iOS 前台类命令入队为 pending。
  3. **命令是否允许**：`resolveNodeCommandAllowlist` + `isNodeCommandAllowed` 检查配置与 node 声明的 `commands`。
  4. **参数清洗**：`sanitizeNodeInvokeParamsForForwarding`（含审批相关字段等）。
  5. **真正下发**：`context.nodeRegistry.invoke({ nodeId, command, params, timeoutMs, idempotencyKey })`。

### 2.2 NodeRegistry.invoke 的链路

- **node-registry.ts**：
  - 根据 nodeId 找到 NodeSession，生成 requestId，组装 `node.invoke.request` 的 payload（id、nodeId、command、paramsJSON、timeoutMs、idempotencyKey）。
  - 通过 **WebSocket** 对该 node 的 `client.socket.send(JSON.stringify({ type: "event", event: "node.invoke.request", payload }))` 下发。
  - 在内存中记录 `pendingInvokes[requestId]`，用 timeoutMs 做超时；等待该 node 通过 **node.invoke.result** 回传结果。
- Node 端（node-host）收到 `node.invoke.request` 后执行命令，再通过 RPC **node.invoke.result** 把 id/nodeId/ok/payload/error 发回 Gateway；Gateway 在 `handleNodeInvokeResult` 里调用 `nodeRegistry.handleInvokeResult`，resolve 对应 Promise，完成一次“在指定 node 上执行命令”。

所以：**在不同 node 上执行命令 = 调用方指定 nodeId + command + params，Gateway 只把请求转发给该 node 的 WebSocket 连接，并等待该 node 的 node.invoke.result**。

### 2.3 Agent 侧：nodes 工具与 system.run

- **工具**：`src/agents/tools/nodes-tool.ts`，支持 `action: "run" | "invoke" | "status" | "describe"` 等。
- **选 node**：通过 `resolveNodeId` / `resolveNodeIdFromList` 等（`src/agents/tools/nodes-utils.ts`），先 `callGatewayTool("node.list", ...)` 拿到节点列表，再按 id/name/ip 解析出 nodeId。
- **执行命令（run）**：
  1. 先 `callGatewayTool("node.invoke", ..., { nodeId, command: "system.run.prepare", params: { command, cwd, agentId, sessionKey } })` 在目标 node 上做“准备”（校验、生成执行计划）。
  2. 再 `callGatewayTool("node.invoke", ..., { nodeId, command: "system.run", params: runParams })` 真正执行；若 node 返回需审批，则走 `exec.approval.request`，用户批准后再带 approved/approvalDecision 重试一次 node.invoke（system.run）。
- **直接调用（invoke）**：对任意 command 调用 `callGatewayTool("node.invoke", ..., { nodeId, command, params, idempotencyKey })`，例如 camera、screen、自定义 command。

因此：**在不同 node 上执行命令** 在 agent 里就是：**先 node.list 解析出 nodeId，再对目标 nodeId 调 node.invoke（或先 system.run.prepare 再 system.run）**。

### 2.4 Node 端（node-host）如何“被执行”

- **连接**：`src/node-host/runner.ts` 里用 `GatewayClient` 连接 Gateway，在 connect 参数里带 `role: "node"`、caps、commands 等；握手成功后 Gateway 侧执行 `nodeRegistry.register(...)`。
- **收请求**：`onEvent` 里只处理 `event === "node.invoke.request"`，payload 交给 `handleInvoke(payload, client, skillBins)`（`src/node-host/invoke.ts`）。
- **回结果**：执行完后通过 `client.request("node.invoke.result", buildNodeInvokeResultParams(frame, result))` 把结果发回 Gateway，Gateway 再交给 `handleNodeInvokeResult` → `nodeRegistry.handleInvokeResult`，完成一次 invoke。

---

## 3. 小结

| 维度 | 说明 |
|------|------|
| **多 node 管理** | 配对信息持久化（node-pairing + 文件），在线状态在 Gateway 内存（NodeRegistry）；node 通过 WebSocket 握手且 role=node 时注册；node.list / node.describe 合并 paired + connected 提供统一列表。 |
| **在不同 node 上执行命令** | 调用方提供 nodeId + command + params；Gateway 的 node.invoke 校验、可选 APNs 唤醒、再通过 NodeRegistry.invoke 经 WebSocket 向该 node 发 node.invoke.request；node 执行后通过 node.invoke.result 回传，Gateway 再返回给调用方。 |
| **Agent 使用方式** | 使用 nodes 工具：node.list 解析 nodeId，再对目标 node 调用 node.invoke（run 场景下先 system.run.prepare 再 system.run，必要时走 exec.approval.request）。 |

---

## 4. 相关文档与代码索引（openclaw 仓库）

- **多 node 管理**：`src/gateway/node-registry.ts`、`src/gateway/server-methods/nodes.ts`（node.list / node.describe / 配对）、`src/gateway/server/ws-connection/message-handler.ts`（register）。
- **跨 node 执行命令**：`src/gateway/server-methods/nodes.ts`（node.invoke handler）、`src/gateway/node-registry.ts`（invoke / handleInvokeResult）、`src/node-host/runner.ts` 与 `invoke.ts`（收 node.invoke.request、回 node.invoke.result）、`src/agents/tools/nodes-tool.ts` 与 `nodes-utils.ts`（Agent 侧调用）。
- **MW4Agent 侧文档**：`docs/architecture/gateway/agent_call_gateway.md`（节点面：nodes 工具与 node.invoke）、`docs/openclaw/dashboard.md`（Dashboard 中 node.list 等 RPC 使用）。
- **工具与技能执行**：`docs/openclaw/tool-and-skill-execution.md`（如何根据用户消息决定执行哪些工具 / skills）。

---

## 5. MW4Agent 简易 node-host

MW4Agent 内置了与 OpenClaw Gateway 兼容的简易 node-host，可将本机注册为一台 node 并执行 `system.run` / `system.run.prepare` 等命令。

**用法与改动总结**见：[docs/architecture/nodes/README.md](../architecture/nodes/README.md)。
