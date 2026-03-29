# MW4Agent AgentRunner 与 LLM 交互实现说明

本文档说明 MW4Agent 中 `AgentRunner` 如何与 LLM 交互，设计参考 OpenClaw 的 `runEmbeddedPiAgent` / `runEmbeddedAttempt`，但实现做了简化。

## 1. 总体架构

```
AgentRunner.run(AgentRunParams)
  ├─ 生成 run_id / session_id / session_key
  ├─ 会话管理：SessionManager.get_or_create_session(...)
  ├─ 生命周期事件：lifecycle start/end/error（StreamEvent）
  ├─ 调度：CommandQueue.enqueue(...) 串行化同一 session 的运行
  └─ _execute_agent_turn(...)
        ├─ 通过 LLM Backend 生成回复（echo / OpenAI）
        ├─ assistant 流事件（delta + final）
        └─ 返回 AgentRunResult（payloads + meta）
```

涉及文件：

- `mw4agent/agents/runner/runner.py`
- `mw4agent/agents/types.py`
- `mw4agent/llm/backends.py`

## 2. AgentRunParams 与元数据

`AgentRunParams` 是一次运行的输入参数：

```python
@dataclass
class AgentRunParams:
    message: str
    run_id: Optional[str] = None
    session_key: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    thinking_level: Optional[str] = None
    timeout_seconds: Optional[int] = None
    extra_system_prompt: Optional[str] = None
    deliver: bool = False
    channel: Optional[str] = None
    images: Optional[List[Dict[str, Any]]] = None
```

`AgentRunMeta` 记录运行结果的元信息（类似 OpenClaw 的 `EmbeddedPiRunMeta`）：

```python
@dataclass
class AgentRunMeta:
    duration_ms: int
    status: AgentRunStatus
    error: Optional[Dict[str, Any]] = None
    stop_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    provider: Optional[str] = None
    model: Optional[str] = None
```

## 3. AgentRunner.run(...) 流程

核心方法：`AgentRunner.run(params: AgentRunParams) -> AgentRunResult`

1. **生成/确定 run/session 标识**
   - `run_id`：优先使用 `params.run_id`（例如 Gateway 已经生成过 runId），否则本地生成 UUID
   - `session_id`：优先使用 `params.session_id`，否则生成 UUID
   - `session_key`：若未显式提供，则使用 `agent:{agent_id}:{session_id}` 模式构造

2. **会话管理**
   - 调用 `SessionManager.get_or_create_session(...)` 保证会话存在

3. **生命周期事件（lifecycle stream）**
   - 使用 `StreamEvent(stream="lifecycle", type="start|end|error")` 通过 `event_stream` 发出：
     - `start`：run 开始
     - `end`：正常结束
     - `error`：运行异常

4. **队列调度（CommandQueue）**
   - 使用 `CommandQueue.enqueue(session_key, run_id, task)` 串行化同一 `session_key` 上的运行，避免并发改写同一会话

5. **执行单次 turn**
   - 核心委托给 `_execute_agent_turn(params, run_id, session_entry)`

## 4. 与 LLM 交互的实现（_execute_agent_turn）

### 4.1 入口函数

```python
async def _execute_agent_turn(
    self,
    params: AgentRunParams,
    run_id: str,
    session_entry: Any,
) -> AgentRunResult:
    ...
```

### 4.2 步骤拆解

1. **assistant 流事件：processing 提示**

- 先发出一条 `assistant` 流事件，供 UI 显示“处理中”的早期反馈：

```python
await self.event_stream.emit(
    StreamEvent(
        stream="assistant",
        type="delta",
        data={"run_id": run_id, "text": "Processing..."},
    )
)
```

2. **调用 LLM Backend**

- 调用 `generate_reply(params)`，由 `mw4agent.llm.backends` 决定具体后端：
  - 默认（无配置）：`echo` 后端（本地生成固定格式回复）
  - 当 `MW4AGENT_LLM_PROVIDER=openai` 且存在 `OPENAI_API_KEY` 时：调用 OpenAI Chat Completions API

返回值为：

```python
reply_text, provider, model, usage = generate_reply(params)
```

3. **assistant 流事件：最终回复**

- 再发一条 `assistant` 事件包含最终文本：

```python
await self.event_stream.emit(
    StreamEvent(
        stream="assistant",
        type="delta",
        data={"run_id": run_id, "text": reply_text, "final": True},
    )
)
```

4. **更新会话元数据**

- 使用 `SessionManager.update_session(...)` 更新 message_count 等信息：

```python
self.session_manager.update_session(
    session_entry.session_id,
    message_count=session_entry.message_count + 1,
)
```

5. **构造 AgentRunResult**

- 计算 `duration_ms`，构造 `AgentPayload` 和 `AgentRunMeta`：

```python
duration_ms = int((time.time() - started) * 1000)

payload = AgentPayload(text=reply_text)

usage_dict = None
if usage has token counters:
    usage_dict = {"input": ..., "output": ..., "total": ...}

return AgentRunResult(
    payloads=[payload],
    meta=AgentRunMeta(
        duration_ms=duration_ms,
        status=AgentRunStatus.COMPLETED,
        provider=provider,
        model=model,
        usage=usage_dict,
    ),
)
```

## 5. LLM Backend 设计（mw4agent.llm.backends）

### 5.1 默认 echo 后端

- 不依赖任何外部服务
- 当 `provider` 未设置或为 `echo/debug` 时使用

行为：

```python
reply = f"Agent (echo) reply: {params.message}"
provider = "echo"
model = params.model or "gpt-4o-mini"
usage = LLMUsage()
```

### 5.2 OpenAI Chat 后端（可选）

当满足以下条件时启用：

- `MW4AGENT_LLM_PROVIDER=openai`
- 环境变量 `OPENAI_API_KEY` 已设置

调用方式（简化版）：

```python
url = "https://api.openai.com/v1/chat/completions"
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
body = {
  "model": model,
  "messages": [{"role": "user", "content": prompt}],
  "temperature": 0.2,
}
```

解析 `choices[0].message.content` 作为回复文本，并填充 `usage` 字段（如果 API 返回了 `usage`）。

如果调用失败（网络异常或 API 错误），后端会 fallback 为 echo 风格的回复，保证 AgentRunner 不至于崩溃。

## 6. 配置方式

当前支持的环境变量：

- `MW4AGENT_LLM_PROVIDER`：
  - `echo`（默认）→ 本地 echo 后端
  - `openai` → 使用 OpenAI Chat API
- `MW4AGENT_LLM_MODEL`：
  - 默认：`gpt-4o-mini`（仅作为字符串标记）
- `OPENAI_API_KEY`：
  - 使用 OpenAI 后端时必须设置

### 6.1 thinking 模式开关（按 provider 注入）

`AgentRunParams.thinking_level` 用于控制“模型端思考模式”是否开启。该字段不会直接决定前端展示；前端展示由 `reasoning_level` 控制。

`thinking_level` 的解析优先级（高到低）：

1. `AgentRunParams.thinking_level`
2. `~/.mw4agent/agents/<agentId>/agent.json` 的 `llm.thinking_level` / `llm.thinkingLevel`
3. `~/.mw4agent/mw4agent.json` 的 `llm.thinking_level` / `llm.thinkingLevel`
4. 环境变量 `MW4AGENT_LLM_THINKING_LEVEL`
5. 默认 `off`

支持值：`off` | `minimal` | `low` | `medium` | `high` | `xhigh` | `adaptive`（兼容：`on -> medium`）。

当 `thinking_level != off` 时，请求体会按 provider 注入不同参数：

| Provider | 注入字段 | 说明 |
|---|---|---|
| `openai` / `deepseek` | `reasoning_effort` | 映射到 `low`/`medium`/`high` |
| `vllm` / `aliyun-bailian` | `reasoning: { effort }` | 映射到 `minimal`/`low`/`medium`/`high`（`xhigh` 会降级为 `high`） |
| 其他 provider | 不注入 | 保持兼容，不强加未知字段 |

配置示例（`~/.mw4agent/mw4agent.json`）：

```json
{
  "llm": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "thinkingLevel": "medium"
  }
}
```

## 7. 使用示例

### 7.1 直接调用 AgentRunner

```python
from mw4agent.agents.runner.runner import AgentRunner
from mw4agent.agents.session.manager import SessionManager
from mw4agent.agents.types import AgentRunParams
import asyncio

async def main():
    session_manager = SessionManager("sessions.json")
    runner = AgentRunner(session_manager)
    res = await runner.run(AgentRunParams(message="你好，介绍一下你自己"))
    print("provider:", res.meta.provider, "model:", res.meta.model)
    print("text:", res.payloads[0].text)

asyncio.run(main())
```

在默认配置下输出类似：

```text
provider: echo model: gpt-4o-mini
text: Agent (echo) reply: 你好，介绍一下你自己
```

### 7.2 启用 OpenAI 后端（示意）

```bash
export MW4AGENT_LLM_PROVIDER=openai
export MW4AGENT_LLM_MODEL=gpt-4o-mini
export OPENAI_API_KEY=sk-...
python3 -m mw4agent channels console run
```

此时 console 通道的回答将来自 OpenAI Chat API。

> 注意：运行时需要具备外网访问能力且遵守对应 API 使用条款。

## 8. 与 OpenClaw 的对比

| 能力 | OpenClaw | MW4Agent（当前实现） |
|------|----------|----------------------|
| 运行封装 | `runEmbeddedPiAgent` + `runEmbeddedAttempt` | `AgentRunner.run` + `_execute_agent_turn` |
| 多 provider 支持 | 统一在 `pi-agent-core` 内部抽象 | 通过 `generate_reply` 简化封装 |
| 工具调用 | 完整 tool schema + tool policy pipeline | 已有 tool registry，后续可接 LLM 工具调用 |
| 流事件 | lifecycle/assistant/tool，细粒度流式 | lifecycle/assistant/tool 三类，基础 delta/final |
| 超时/重试/compaction | 完整的 attempt loop + compaction | 目前未实现，后续可引入 |

