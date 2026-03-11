# MW4Agent 智能体系统文档

本目录包含 MW4Agent 智能体执行系统的设计文档。

## 文档列表

- [架构文档](./architecture.md) - 智能体执行系统的完整架构说明

## 快速开始

```python
from mw4agent.agents import AgentRunner
from mw4agent.agents.session import SessionManager
from mw4agent.agents.types import AgentRunParams

# 初始化
session_manager = SessionManager("sessions.json")
runner = AgentRunner(session_manager)

# 运行智能体
params = AgentRunParams(
    message="Hello!",
    session_id="session_123",
    agent_id="main",
)

result = await runner.run(params)
print(result.payloads[0].text)
```

## 参考

- OpenClaw Agents 系统：`src/agents/` 目录
- OpenClaw Agent Loop 文档：`docs/concepts/agent-loop.md`
