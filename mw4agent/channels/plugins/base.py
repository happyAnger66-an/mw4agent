"""ChannelPlugin base protocol."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from ..dock import ChannelDock
from ..types import ChannelCapabilities, ChannelId, ChannelMeta, InboundContext, OutboundPayload


DeliverFn = Callable[[OutboundPayload], Awaitable[None]]
InboundHandler = Callable[[InboundContext], Awaitable[None]]


@dataclass(frozen=True)
class ChannelPlugin:
    """A channel plugin bundles meta + capabilities + monitor/outbound hooks.

    This is the 'heavy' side: concrete plugins can depend on SDKs.
    """

    id: ChannelId
    meta: ChannelMeta
    capabilities: ChannelCapabilities
    dock: ChannelDock

    @abc.abstractmethod
    async def run_monitor(self, *, on_inbound: InboundHandler) -> None:
        """Start monitoring inbound messages and invoke on_inbound for each inbound event."""

    @abc.abstractmethod
    async def deliver(self, payload: OutboundPayload) -> None:
        """Deliver an outbound payload."""

