# Session Memory 后端插件化设计（Memory Backends）

本文在 `docs/architecture/agent/session_memory.md` 的基础上，抽象出 **会话短期记忆（Session Memory）** 的插件化接口，使 `mw4agent` 可以像 OpenClaw 一样支持多种 session memory 实现（本地 JSONL、数据库、增强型 memory 等），而 `AgentRunner` 只依赖统一接口。

## 设计目标

- **解耦存储细节**：`AgentRunner` 不直接依赖 JSONL 文件 / 路径规则，只与抽象接口交互。
- **可插拔**：支持通过配置选择不同的 memory backend（例如：`jsonl` / `lite` / `db`）。
- **兼容现有实现**：当前的 JSONL transcript + 截断逻辑可以作为默认 backend 落地。
- **支持演进**：后续可以在不改 AgentRunner 的前提下，引入更复杂的短期记忆策略（如携带部分工具结果、附带 extra metadata 等）。

## 核心概念与数据结构

### `SessionTurn`：一次对话片段的抽象

```python
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class SessionTurn:
    """一轮对话要持久化/加载的最小单元."""
    role: str                    # "system" | "user" | "assistant" | "tool"
    content: Any                 # 通常为 str，或 OpenAI-style content blocks
    extra: Dict[str, Any] = None # 例如 tool_call_id / tool_calls / usage 等
```

- 将 **“要存到 transcript 里的一条 message”** 提取为通用结构：
  - `role`：语义角色
  - `content`：显示内容（可以是简单字符串，也可以是 block list）
  - `extra`：用于承载工具调用元信息、usage 统计等，不强制 schema
- 各种 backend 可以在内部自由映射到实际落盘格式（JSONL / DB / 远程服务）。

### `SessionMemoryConfig`：与配置解耦的 Memory 配置

```python
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SessionMemoryConfig:
    """抽象的 memory 配置（由 root config / env 映射而来）."""
    history_limit_turns: Optional[int] = None
    # 预留字段，供不同 backend 使用：
    extra: Dict[str, Any] = None
```

- 目前只明确一个字段：
  - `history_limit_turns`：按用户回合数截断历史（等价于 OpenClaw 的 `dmHistoryLimit` / `historyLimit` 思路）。
- `extra` 预留给后续 backend 使用，例如：
  - 每个 channel 的不同 historyLimit
  - 针对特定 provider/model 的特殊策略

## Session Memory 插件接口：`SessionMemoryBackend`

```python
from typing import Iterable, List, Optional


class SessionMemoryBackend:
    """Session 短期记忆插件接口."""

    def load_history(
        self,
        *,
        agent_id: Optional[str],
        session_id: str,
        session_key: Optional[str],
        config: SessionMemoryConfig,
    ) -> List[SessionTurn]:
        """
        加载当前 session 的短期记忆。

        - 内部负责加载底层存储（JSONL/DB/远程）；
        - 在返回前完成必要的“治理”：
          - 去掉 orphan user（尾部 user 且无对应 assistant）；
          - 按 history_limit_turns 截断；
          - 历史修复（如需要）。
        - 返回值是按时间顺序排列、可直接用于构造 LLM messages 的 turns。
        """

    def append_turns(
        self,
        *,
        agent_id: Optional[str],
        session_id: str,
        session_key: Optional[str],
        cwd: str,
        turns: Iterable[SessionTurn],
    ) -> None:
        """
        将一轮对话产生的新 turns 追加到存储中。

        - 典型调用时机：
          - user 消息发送后：追加 `{role: \"user\", ...}`；
          - LLM 返回 assistant：追加 `{role: \"assistant\", ...}`；
          - 工具调用时：追加 `{role: \"assistant\", tool_calls...}` 和后续 `{role: \"tool\", ...}`。
        """

    def sanitize_for_provider(
        self,
        *,
        turns: List[SessionTurn],
        provider: str,
        model: str,
    ) -> List[SessionTurn]:
        """
        针对特定 provider/model 的附加治理（可选）。

        - 默认实现可以直接返回原 turns；
        - 针对严格模型，可以在这里实现：
          - 删除/折叠历史 thinking 块；
          - 调整 tool_call_id 格式；
          - 做 provider 特定的 message 规整。
        """
```

> 约定：  
> - 所有 session memory backend **都只处理“短期记忆”的部分**，不负责 system prompt 构造，也不负责长期 memory（memory tools）。  
> - `AgentRunner` 在构造最终 messages 时，会在 `load_history()` 返回的 turns 前后，再拼接 system prompt / 本轮 prompt。

## 插件注册与选择：`SessionMemoryRegistry`

为了支持多实现并通过配置选择，我们引入一个简单的注册表：

```python
# mw4agent/agents/session/memory_registry.py

from typing import Callable, Dict

MemoryBackendFactory = Callable[[SessionMemoryConfig], SessionMemoryBackend]


class SessionMemoryRegistry:
    def __init__(self) -> None:
        self._backends: Dict[str, MemoryBackendFactory] = {}

    def register(self, name: str, factory: MemoryBackendFactory) -> None:
        self._backends[name] = factory

    def create(self, name: str, config: SessionMemoryConfig) -> SessionMemoryBackend:
        if name not in self._backends:
            raise ValueError(f\"Unknown session memory backend: {name}\")
        return self._backends[name](config)


_registry = SessionMemoryRegistry()


def get_session_memory_registry() -> SessionMemoryRegistry:
    return _registry
```

未来配置示例（`~/.mw4agent/mw4agent.json`）：

```jsonc
{
  "session": {
    "memory": {
      "backend": "jsonl"
    },
    "historyLimitTurns": 4
  }
}
```

`AgentRunner` 启动时读取 root config，通过 `SessionMemoryRegistry` 创建对应 backend 实例：

```python
cfg = get_default_config_manager().read_config("mw4agent", default={})
session_cfg = cfg.get("session") or {}
backend_name = (session_cfg.get("memory") or {}).get("backend", "jsonl")
memory_cfg = SessionMemoryConfig(
    history_limit_turns=session_cfg.get("historyLimitTurns") or session_cfg.get("history_limit_turns"),
    extra=session_cfg,
)
memory_backend = get_session_memory_registry().create(backend_name, memory_cfg)
```

## 默认实现建议：JSONL Transcript Backend

当前仓库已经实现了一套基于 JSONL transcript 的 session memory（见 `mw4agent/agents/session/transcript.py` + `AgentRunner` 中的注入逻辑）。后续可以将其重构为一个默认 backend，例如：

```python
class JsonlSessionMemoryBackend(SessionMemoryBackend):
    def __init__(self, config: SessionMemoryConfig) -> None:
        self.config = config

    def load_history(...):  # 内部复用 read_messages / drop_trailing_orphan_user / limit_history_user_turns
        ...

    def append_turns(...):  # 内部复用 resolve_session_transcript_path + append_messages
        ...
```

注册为默认实现：

```python
from .memory_registry import get_session_memory_registry

get_session_memory_registry().register("jsonl", lambda cfg: JsonlSessionMemoryBackend(cfg))
```

这样，`AgentRunner` 将仅依赖 `SessionMemoryBackend` 接口，而不会再直接操作 transcript 文件路径与 JSONL 细节，从而完成 **Session Memory 的插件化抽象**。***
