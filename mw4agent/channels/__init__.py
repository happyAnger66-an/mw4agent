"""MW4Agent channels layer (OpenClaw-inspired).

Key ideas:
- ChannelDock: lightweight policies/capabilities used by shared code paths
- ChannelPlugin: heavy adapter (monitor, outbound delivery, auth/pairing later)
- InboundContext: normalized message envelope
- Dispatcher: turns inbound messages into agent runs and outbound payloads
"""

from .types import (
    ChannelCapabilities,
    ChannelId,
    ChannelMeta,
    InboundContext,
    OutboundPayload,
)
from .dock import ChannelDock
from .registry import ChannelRegistry, get_channel_registry
from .dispatcher import ChannelDispatcher, ChannelRuntime

__all__ = [
    "ChannelId",
    "ChannelMeta",
    "ChannelCapabilities",
    "InboundContext",
    "OutboundPayload",
    "ChannelDock",
    "ChannelRegistry",
    "get_channel_registry",
    "ChannelDispatcher",
    "ChannelRuntime",
]

