# MW4Agent 智能体执行系统架构

本文档说明 MW4Agent 智能体执行系统的架构设计，参考 OpenClaw 的 agents 系统实现。

## 1. 系统概述

MW4Agent 智能体执行系统是一个**可扩展的智能体运行时**，支持：

- **会话管理**：管理智能体会话和对话历史
- **工具系统**：可扩展的工具注册和执行机制
- **事件流**：实时事件流和生命周期管理
- **队列系统**：会话级别的运行序列化
- **LLM 集成**：与各种 LLM 提供商集成

### 架构图

```
┌─────────────────────────────────────────────────────────┐
│              Agent Runner (核心执行引擎)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ Session Mgr  │  │ Tool Registry│  │ Event Stream │ │
│  └──────────────┘  └──────────────┘  └──────────────┘ │
│  ┌──────────────┐  ┌──────────────┐                    │
│  │ Command Queue│  │ LLM Provider│                    │
│  └──────────────┘  └──────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

## 2. 核心组件

### 2.1 Agent Runner (`agents/runner/runner.py`)

**作用：** 智能体执行的核心引擎，类似于 OpenClaw 的 `runEmbeddedPiAgent`

**主要功能：**
- 执行智能体回合（agent turn）
- 管理运行生命周期
- 协调工具执行
- 处理事件流

**关键方法：**

```python
class AgentRunner:
    async def run(params: AgentRunParams) -> AgentRunResult
    async def execute_tool(tool_call_id, tool_name, params, context) -> Any
```

**执行流程：**

1. **接收请求**：接收 `AgentRunParams`
2. **会话解析**：获取或创建会话
3. **队列序列化**：通过 CommandQueue 序列化执行
4. **生命周期事件**：发出 start/end/error 事件
5. **LLM 交互**：调用 LLM 提供商（待实现）
6. **工具执行**：执行工具调用
7. **结果返回**：返回 `AgentRunResult`

### 2.2 Session Manager (`agents/session/manager.py`)

**作用：** 管理智能体会话，类似于 OpenClaw 的 SessionManager

**主要功能：**
- 会话创建和获取
- 会话元数据管理
- 会话持久化（JSON 文件）
- 会话列表和查询

**数据结构：**

```python
@dataclass
class SessionEntry:
    session_id: str
    session_key: str
    agent_id: Optional[str]
    created_at: int
    updated_at: int
    message_count: int
    total_tokens: int
    metadata: Dict[str, Any]
```

**使用示例：**

```python
manager = SessionManager("sessions.json")
session = manager.get_or_create_session(
    session_id="123",
    session_key="agent:main:123",
    agent_id="main",
)
manager.update_session("123", message_count=session.message_count + 1)
```

### 2.3 Tool System (`agents/tools/`)

**作用：** 可扩展的工具系统，类似于 OpenClaw 的工具系统

**组件：**

1. **AgentTool** (`base.py`)：工具基类
2. **ToolRegistry** (`registry.py`)：工具注册表
3. **ToolResult**：工具执行结果

**工具定义：**

```python
class AgentTool(ABC):
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema
    owner_only: bool
    
    @abstractmethod
    async def execute(tool_call_id, params, context) -> ToolResult
```

**工具注册：**

```python
registry = get_tool_registry()
tool = EchoTool()
registry.register(tool)
```

**工具执行：**

```python
result = await runner.execute_tool(
    tool_call_id="call_123",
    tool_name="echo",
    params={"text": "Hello"},
    context={"session_key": "agent:main:123"},
)
```

### 2.4 Event Stream (`agents/events/stream.py`)

**作用：** 事件流系统，类似于 OpenClaw 的 `subscribeEmbeddedPiSession`

**事件类型：**

- **`assistant`**：助手回复流（delta、complete）
- **`tool`**：工具执行事件（start、update、end、error）
- **`lifecycle`**：生命周期事件（start、end、error）

**事件结构：**

```python
@dataclass
class StreamEvent:
    stream: str  # "assistant" | "tool" | "lifecycle"
    type: str    # event type
    data: Dict[str, Any]
    timestamp: int
```

**使用示例：**

```python
stream = EventStream()

# Subscribe to events
async def handle_assistant(event: StreamEvent):
    print(f"Assistant: {event.data.get('text')}")

stream.subscribe("assistant", handle_assistant)

# Emit events
await stream.emit(StreamEvent(
    stream="assistant",
    type="delta",
    data={"text": "Hello"},
))
```

### 2.5 Command Queue (`agents/queue/manager.py`)

**作用：** 命令队列系统，序列化智能体运行

**设计：**
- **会话级队列**：每个 session_key 有独立的队列
- **全局队列**（可选）：跨会话序列化
- **活跃运行跟踪**：防止并发执行

**使用场景：**
- 防止同一会话的并发运行
- 保持会话历史一致性
- 工具/会话竞态条件防护

**使用示例：**

```python
queue = CommandQueue()

result = await queue.enqueue(
    session_key="agent:main:123",
    run_id="run_456",
    task=lambda: runner.run(params),
)
```

## 3. 数据流

### 3.1 智能体执行流程

```
用户消息
  ↓
AgentRunner.run()
  ↓
CommandQueue.enqueue()  (序列化)
  ↓
_execute_agent_turn()
  ↓
EventStream.emit(lifecycle:start)
  ↓
LLM 调用 (待实现)
  ↓
工具调用 (如需要)
  ↓
EventStream.emit(assistant:delta)
  ↓
EventStream.emit(lifecycle:end)
  ↓
返回 AgentRunResult
```

### 3.2 工具执行流程

```
工具调用请求
  ↓
AgentRunner.execute_tool()
  ↓
ToolRegistry.get_tool()
  ↓
EventStream.emit(tool:start)
  ↓
Tool.execute()
  ↓
EventStream.emit(tool:end)
  ↓
返回 ToolResult
```

### 3.3 事件流

```
事件产生
  ↓
EventStream.emit()
  ↓
通知订阅者 (stream-specific)
  ↓
通知通用处理器 (StreamHandler)
  ↓
事件存储 (可选)
```

## 4. 类型系统

### 4.1 AgentRunParams

```python
@dataclass
class AgentRunParams:
    message: str
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

### 4.2 AgentRunResult

```python
@dataclass
class AgentRunResult:
    payloads: List[AgentPayload]
    meta: AgentRunMeta
    did_send_via_messaging_tool: bool = False
```

### 4.3 AgentRunMeta

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

## 5. 与 OpenClaw 的对比

| 组件 | OpenClaw (TypeScript) | MW4Agent (Python) |
|------|----------------------|-------------------|
| 执行引擎 | `runEmbeddedPiAgent` | `AgentRunner.run()` |
| 会话管理 | `SessionManager` | `SessionManager` |
| 工具系统 | `AnyAgentTool` + Registry | `AgentTool` + `ToolRegistry` |
| 事件流 | `subscribeEmbeddedPiSession` | `EventStream` |
| 队列系统 | `enqueueCommandInLane` | `CommandQueue` |
| LLM 集成 | pi-agent-core | 待实现 |

## 6. 使用示例

### 6.1 基本使用

```python
from mw4agent.agents import AgentRunner
from mw4agent.agents.session import SessionManager
from mw4agent.agents.types import AgentRunParams

# 初始化
session_manager = SessionManager("sessions.json")
runner = AgentRunner(session_manager)

# 运行智能体
params = AgentRunParams(
    message="Hello, agent!",
    session_id="session_123",
    agent_id="main",
    model="gpt-4",
    provider="openai",
)

result = await runner.run(params)
print(result.payloads[0].text)
```

### 6.2 工具注册和使用

```python
from mw4agent.agents.tools import get_tool_registry
from mw4agent.agents.tools.examples import EchoTool

# 注册工具
registry = get_tool_registry()
registry.register(EchoTool())

# 工具会在 LLM 调用时自动可用
```

### 6.3 事件订阅

```python
from mw4agent.agents.events import EventStream, StreamEvent

stream = EventStream()

async def handle_assistant(event: StreamEvent):
    if event.type == "delta":
        print(f"Assistant: {event.data.get('text')}")

stream.subscribe("assistant", handle_assistant)

# 运行时会自动发出事件
```

## 7. 扩展点

### 7.1 添加新工具

```python
from mw4agent.agents.tools import AgentTool, ToolResult

class MyTool(AgentTool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="My custom tool",
            parameters={
                "type": "object",
                "properties": {
                    "param": {"type": "string"}
                },
            },
        )
    
    async def execute(self, tool_call_id, params, context):
        # 实现工具逻辑
        return ToolResult(success=True, result={"output": "..."})

# 注册
registry = get_tool_registry()
registry.register(MyTool())
```

### 7.2 自定义事件处理器

```python
from mw4agent.agents.events import StreamHandler, StreamEvent

class MyHandler(StreamHandler):
    async def handle(self, event: StreamEvent):
        # 处理事件
        pass

stream = EventStream()
stream.add_handler(MyHandler())
```

### 7.3 集成 LLM 提供商

**待实现：** LLM 提供商集成层

需要实现：
- Provider 接口
- Model 配置
- 流式响应处理
- Token 使用统计

## 8. 设计原则

1. **可扩展性**：通过注册表模式扩展工具和功能
2. **类型安全**：使用类型提示和 dataclass
3. **异步优先**：所有 I/O 操作使用 async/await
4. **事件驱动**：通过事件流实现解耦
5. **会话隔离**：每个会话独立管理状态

## 9. 待实现功能

### 9.1 LLM 集成

- [ ] Provider 接口定义
- [ ] OpenAI 集成
- [ ] Anthropic 集成
- [ ] 其他提供商集成
- [ ] 流式响应处理
- [ ] Token 使用统计

### 9.2 高级功能

- [ ] 工具调用循环检测
- [ ] 会话压缩（compaction）
- [ ] 子智能体（subagent）支持
- [ ] 超时和重试机制
- [ ] 错误恢复

### 9.3 性能优化

- [ ] 会话缓存
- [ ] 工具结果缓存
- [ ] 批量工具执行
- [ ] 并发控制优化

## 10. 参考

- OpenClaw 源码：
  - `src/agents/pi-embedded-runner.ts` - 核心执行引擎
  - `src/agents/pi-embedded-runner/run/attempt.ts` - 执行尝试
  - `src/agents/pi-embedded-subscribe.ts` - 事件订阅
  - `src/gateway/server-methods/agent.ts` - Gateway RPC 处理
- OpenClaw 文档：
  - [Agent Loop](/concepts/agent-loop.md)
  - [System Prompt](/concepts/system-prompt.md)
  - [Streaming](/concepts/streaming.md)

## 11. 总结

MW4Agent 智能体执行系统参考 OpenClaw 的设计，提供了：

- ✅ **核心执行引擎**：AgentRunner
- ✅ **会话管理**：SessionManager
- ✅ **工具系统**：可扩展的工具注册和执行
- ✅ **事件流**：实时事件订阅和通知
- ✅ **队列系统**：会话级序列化

系统采用**模块化、可扩展、事件驱动**的设计，为后续的 LLM 集成和高级功能奠定了基础。
