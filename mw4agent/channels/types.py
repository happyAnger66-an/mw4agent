"""Channels core types (OpenClaw-inspired).

We keep these minimal and stable so shared code paths can depend on them without
pulling in channel SDKs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

ChannelId = str


@dataclass(frozen=True)
class ChannelMeta:
    id: ChannelId
    label: str
    docs_path: Optional[str] = None


@dataclass(frozen=True)
class ChannelCapabilities:
    """Capabilities used for gating UI/behavior (roughly like OpenClaw)."""

    chat_types: tuple[Literal["direct", "group", "channel", "thread"], ...] = ("direct", "group")
    native_commands: bool = False
    block_streaming: bool = False


@dataclass(frozen=True)
class InboundContext:
    """Normalized inbound envelope.

    This is MW4Agent's equivalent of OpenClaw's MsgContext/FinalizedMsgContext
    (greatly simplified for the first console channel).
    """

    channel: ChannelId
    text: str

    # Routing/session
    session_key: str
    session_id: str
    agent_id: str = "main"

    # Identity + gating
    chat_type: Literal["direct", "group", "channel", "thread"] = "direct"
    was_mentioned: bool = True
    command_authorized: bool = True
    sender_is_owner: bool = True

    # Optional structured metadata
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    to: Optional[str] = None
    thread_id: Optional[str] = None
    timestamp_ms: Optional[int] = None

    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboundPayload:
    """Outbound payload produced by the dispatcher."""

    text: str
    is_error: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

