# MW4Agent（Python）Gateway：Agent 与 Gateway 完整交互流程

本文档描述 MW4Agent 中按 OpenClaw 风格实现的 Python Gateway，目标是跑通：

- RPC `agent`：触发一次智能体 run（立即返回 accepted + runId）
- RPC `agent.wait`：等待 run 的 lifecycle 终态（ok/error/timeout）
- WebSocket `/ws`：广播 agent 的事件流（lifecycle/assistant/tool）

并说明其内部状态机（dedupe、run registry）与 `AgentRunner` 的桥接方式。

## 1. 代码位置

### Gateway

- `mw4agent/gateway/server.py`：FastAPI app（`/rpc`、`/health`、`/ws`）
- `mw4agent/gateway/state.py`：内存状态（dedupe、runs、ws clients）
- `mw4agent/gateway/types.py`：协议类型（AgentEvent 等）
- `mw4agent/gateway/client.py`：最小 HTTP RPC client（CLI 调用）

### Agent

- `mw4agent/agents/runner/runner.py`：`AgentRunner.run(...)`（支持传入 `run_id`）
- `mw4agent/agents/events/stream.py`：内部事件流（gateway 订阅后转发）

## 2. 外部接口（对齐 OpenClaw）

### 2.1 HTTP Health

- `GET /health` → `{ ok, ts, runs }`

### 2.2 HTTP JSON RPC

- `POST /rpc`

请求体（简化的 JSON-RPC 风格）：

```json
{ "id": "uuid", "method": "agent", "params": { ... } }
```

响应体（统一格式）：

```json
{ "id": "uuid", "ok": true, "payload": { ... }, "runId": "..." }
```

#### `method=agent`

必要参数：

- `message`: string
- `idempotencyKey`: string（用于 dedupe，避免重试重复执行）

可选参数：

- `runId`: string（若不传由 gateway 生成）
- `sessionKey` / `sessionId` / `agentId`
- `deliver` / `channel` / `extraSystemPrompt`
- **`thinkingLevel`**: string，控制模型端扩展思考强度。可选值：`off` | `minimal` | `low` | `medium` | `high` | `xhigh` | `adaptive`（详见 [Thinking 模式](../openclaw/thinking-mode.md)）。
- **`reasoningLevel`**: string，控制是否向客户端展示推理块。可选值：`off`（隐藏）| `on` | `stream`（流式展示）。

返回：

- 立即返回 `{ runId, status:"accepted", acceptedAt }`
- 后台启动 agent run
- run 结束后会在 dedupe 中写入终态（用于后续快速返回/诊断）

#### `method=agent.wait`

必要参数：

- `runId`

可选参数：

- `timeoutMs`（默认 30s）

返回：

- `{ status:"ok", startedAt, endedAt }`
- 或 `{ status:"error", error, endedAt }`
- 或 `{ status:"timeout" }`

### 2.3 WebSocket 事件流

- `WS /ws`

消息体为 JSON：

```json
{
  "run_id": "uuid",
  "stream": "lifecycle|assistant|tool",
  "data": { ... },
  "seq": 1,
  "ts": 1710000000000
}
```

当前最小实现会产生：

- `lifecycle`：`phase=start|end|error`
- `assistant`：`type=delta`（来自 `AgentRunner` 的占位实现）
- `tool`：暂未触发（后续接入真实 tools loop 时会自然产生）

## 3. 内部架构与关键逻辑

## 3.1 Dedupe（幂等去重）

Gateway 在 `agent` RPC 中要求 `idempotencyKey`，并以 `agent:{idempotencyKey}` 作为 key 写入 dedupe：

- **第一次请求**：写入 `accepted`，并触发后台执行
- **重试请求**：直接返回 dedupe 中的 cached payload（避免重复 run）
- **执行完成**：用同一 key 写入终态（ok/error），便于后续查询

对应 OpenClaw：`context.dedupe` + `setGatewayDedupeEntry(...)`。

## 3.2 Run Registry（runId → terminal snapshot）

GatewayState 持有：

- `runs[runId] -> RunRecord(done Event + snapshot + seq + started_at_ms)`
- `ws_clients`：订阅者队列集合

`agent.wait` 的实现：

1. 如果 `RunRecord.snapshot` 已有终态 → 直接返回
2. 否则等待 `RunRecord.done`（最多 `timeoutMs`）

对应 OpenClaw：`waitForAgentJob(...)` 的 lifecycle 监听 + terminal snapshot 缓存。

## 3.3 AgentRunner 事件桥接

`create_app()` 在启动时：

- 创建 `AgentRunner`
- 订阅 `runner.event_stream` 的三类 stream：`lifecycle` / `assistant` / `tool`
- 将内部 `StreamEvent` 转换为 Gateway `AgentEvent` 并 `broadcast(...)`
- 在 `lifecycle end/error` 时写入 run terminal snapshot 并唤醒 waiters

关键点（与 OpenClaw 一致）：**runId 必须贯穿整个链路**。因此 MW4Agent 的 `AgentRunParams` 支持可选 `run_id`，gateway 生成的 `runId` 会传入 `AgentRunner.run(...)`，保证事件与 wait 统一关联。

## 4. CLI 使用方式

### 4.1 启动 Gateway

```bash
python3 -m mw4agent gateway run --bind 127.0.0.1 --port 28790 --session-file /tmp/mw4agent.sessions.json
```

### 4.2 探测状态

```bash
python3 -m mw4agent gateway status --url http://127.0.0.1:28790 --json
python3 -m mw4agent gateway health --url http://127.0.0.1:28790 --json
```

### 4.3 调用 RPC（示例：agent / agent.wait）

```bash
python3 -m mw4agent gateway call agent --url http://127.0.0.1:28790 --params '{\"message\":\"hi\",\"sessionKey\":\"rpc:demo\",\"sessionId\":\"demo\",\"agentId\":\"main\",\"idempotencyKey\":\"<uuid>\"}' --json
python3 -m mw4agent gateway call agent.wait --url http://127.0.0.1:28790 --params '{\"runId\":\"<runId>\",\"timeoutMs\":5000}' --json
```

## 5. 与 OpenClaw 的对应关系（简表）

| OpenClaw | MW4Agent（本实现） |
|---|---|
| `server-methods/agent.ts` 的 `agent` / `agent.wait` | `mw4agent/gateway/server.py` 的 `/rpc` 分发 |
| `context.dedupe` | `GatewayState.dedupe` |
| `waitForAgentJob`（监听 lifecycle） | `RunRecord.done + snapshot`（由 lifecycle bridge 驱动） |
| `emitAgentEvent` + `createAgentEventHandler` 广播 | `AgentRunner.event_stream` → `GatewayState.broadcast` |

## 6. 下一步（贴近 OpenClaw 的增强点）

- `agent.wait` 增加 “dedupe terminal snapshot” 的竞争策略（类似 OpenClaw 的 lifecycle vs dedupe race）
- 引入认证 token、访问控制、rate limit
- 持久化 run snapshots（避免重启丢失）
- 增加 tool execution 事件与结构化错误码体系

