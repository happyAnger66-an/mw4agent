"""MW4Agent plugin system: discover and load plugins (tools, skills, hooks)."""

from .loader import (
    discover_plugins,
    load_plugins,
    get_plugin_skill_source,
    PluginInfo,
    PluginSkillSource,
)

__all__ = [
    "discover_plugins",
    "load_plugins",
    "get_plugin_skill_source",
    "PluginInfo",
    "PluginSkillSource",
]
