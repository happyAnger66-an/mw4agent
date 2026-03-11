"""Event streaming - similar to OpenClaw's stream events"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
import time


@dataclass
class StreamEvent:
    """Stream event"""
    stream: str  # "assistant" | "tool" | "lifecycle"
    type: str  # event type
    data: Dict[str, Any]
    timestamp: int = 0

    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = int(time.time() * 1000)


class StreamHandler(ABC):
    """Base class for stream event handlers"""

    @abstractmethod
    async def handle(self, event: StreamEvent) -> None:
        """Handle a stream event"""
        pass


class EventStream:
    """
    Event Stream - manages event subscription and emission
    
    Similar to OpenClaw's subscribeEmbeddedPiSession that bridges
    pi-agent-core events to OpenClaw agent stream.
    """

    def __init__(self):
        self._handlers: List[StreamHandler] = []
        self._subscribers: Dict[str, List[Callable]] = {}  # stream -> handlers
        self._events: List[StreamEvent] = []

    def subscribe(self, stream: str, handler: Callable[[StreamEvent], None]) -> None:
        """Subscribe to a stream"""
        if stream not in self._subscribers:
            self._subscribers[stream] = []
        self._subscribers[stream].append(handler)

    def add_handler(self, handler: StreamHandler) -> None:
        """Add a stream handler"""
        self._handlers.append(handler)

    async def emit(self, event: StreamEvent) -> None:
        """Emit an event"""
        self._events.append(event)

        # Notify stream-specific subscribers
        if event.stream in self._subscribers:
            for handler in self._subscribers[event.stream]:
                try:
                    await handler(event) if asyncio.iscoroutinefunction(handler) else handler(event)
                except Exception as e:
                    print(f"Error in stream handler: {e}")

        # Notify general handlers
        for handler in self._handlers:
            try:
                await handler.handle(event)
            except Exception as e:
                print(f"Error in event handler: {e}")

    def get_events(self, stream: Optional[str] = None) -> List[StreamEvent]:
        """Get events, optionally filtered by stream"""
        if stream:
            return [e for e in self._events if e.stream == stream]
        return self._events.copy()

    def clear(self) -> None:
        """Clear all events"""
        self._events.clear()
