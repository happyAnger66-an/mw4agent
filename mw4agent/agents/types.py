"""Agent execution types - similar to OpenClaw's agent types"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from enum import Enum


class AgentRunStatus(str, Enum):
    """Agent run status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    TIMEOUT = "timeout"
    ABORTED = "aborted"


class LifecyclePhase(str, Enum):
    """Lifecycle phase"""
    START = "start"
    END = "end"
    ERROR = "error"


@dataclass
class AgentRunMeta:
    """Agent run metadata - similar to EmbeddedPiRunMeta"""
    duration_ms: int
    status: AgentRunStatus
    error: Optional[Dict[str, Any]] = None
    stop_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    # Provider and model info
    provider: Optional[str] = None
    model: Optional[str] = None


@dataclass
class AgentPayload:
    """Agent response payload"""
    text: Optional[str] = None
    media_url: Optional[str] = None
    media_urls: Optional[List[str]] = None
    reply_to_id: Optional[str] = None
    is_error: bool = False


@dataclass
class AgentRunResult:
    """Agent run result - similar to EmbeddedPiRunResult"""
    payloads: List[AgentPayload]
    meta: AgentRunMeta
    did_send_via_messaging_tool: bool = False


@dataclass
class AgentRunParams:
    """Parameters for running an agent"""
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
    # High-level channel / source (feishu | telegram | webhook | console | internal ...)
    channel: Optional[str] = None
    # Caller identity & gating (used for tool permissions)
    sender_id: Optional[str] = None
    sender_is_owner: Optional[bool] = None
    command_authorized: Optional[bool] = None
    images: Optional[List[Dict[str, Any]]] = None
    """Workspace root for read/write/memory tools and transcript cwd when unset.

    Default: ``~/.mw4agent/agents/<agentId>/workspace`` (see resolve_agent_workspace_dir),
    or ``MW4AGENT_WORKSPACE_DIR`` when that env is set (global override).
    """
    workspace_dir: Optional[str] = None
    """Reasoning visibility: off (hide) | on | stream. When on/stream, reasoning blocks are emitted and frontend may show them."""
    reasoning_level: Optional[str] = None


@dataclass
class ToolCall:
    """Tool call definition"""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ToolResult:
    """Tool execution result"""
    tool_call_id: str
    tool_name: str
    result: Any
    error: Optional[str] = None


@dataclass
class StreamEvent:
    """Stream event - similar to OpenClaw's stream events"""
    stream: str  # "assistant" | "tool" | "lifecycle"
    type: str  # event type
    data: Dict[str, Any]
    timestamp: int = 0

    def __post_init__(self) -> None:
        if self.timestamp == 0:
            import time

            self.timestamp = int(time.time() * 1000)
