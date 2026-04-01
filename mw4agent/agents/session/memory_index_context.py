"""Context for which ``agent_workspace_dir`` indexes session transcript chunks (SQLite).

Set for the duration of :meth:`mw4agent.agents.runner.runner.AgentRunner._execute_agent_turn`
so :func:`mw4agent.agents.session.transcript._notify_transcript_index_delta` can target the
same DB as file-based MEMORY.md for that run (e.g. orchestration-scoped workspace).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Optional

_memory_index_workspace_dir: ContextVar[Optional[str]] = ContextVar(
    "memory_index_workspace_dir", default=None
)


def memory_index_workspace_set(workspace_dir: Optional[str]) -> Any:
    """Return a token for :func:`memory_index_workspace_reset`."""
    return _memory_index_workspace_dir.set(workspace_dir)


def memory_index_workspace_reset(token: Any) -> None:
    _memory_index_workspace_dir.reset(token)


def current_memory_index_workspace_dir() -> Optional[str]:
    return _memory_index_workspace_dir.get()
