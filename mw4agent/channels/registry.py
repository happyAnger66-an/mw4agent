"""Channel registry (OpenClaw-inspired).

- Dock: lightweight policies used by shared code paths
  - In MW4Agent we keep dock on the plugin for now
- Plugin: heavy adapter providing monitor + deliver
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .dock import ChannelDock
from .types import ChannelId
from .plugins.base import ChannelPlugin


@dataclass
class ChannelRegistry:
    def __init__(self) -> None:
        self._plugins: Dict[ChannelId, ChannelPlugin] = {}
        self._docks: Dict[ChannelId, ChannelDock] = {}

    def register_plugin(self, plugin: ChannelPlugin) -> None:
        if plugin.id in self._plugins:
            raise ValueError(f"Channel plugin already registered: {plugin.id}")
        self._plugins[plugin.id] = plugin
        self._docks[plugin.id] = plugin.dock

    def get_plugin(self, channel_id: ChannelId) -> Optional[ChannelPlugin]:
        return self._plugins.get(channel_id)

    def get_dock(self, channel_id: ChannelId) -> Optional[ChannelDock]:
        return self._docks.get(channel_id)

    def list_channel_ids(self) -> List[ChannelId]:
        return list(self._plugins.keys())


_registry = ChannelRegistry()


def get_channel_registry() -> ChannelRegistry:
    return _registry

