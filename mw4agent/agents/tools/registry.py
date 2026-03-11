"""Tool registry - manages agent tools"""

from typing import Any, Dict, List, Optional
from .base import AgentTool


class ToolRegistry:
    """Tool registry - manages tool registration and discovery"""

    def __init__(self):
        self._tools: Dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        """Register a tool"""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Unregister a tool"""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get_tool(self, name: str) -> Optional[AgentTool]:
        """Get a tool by name"""
        return self._tools.get(name)

    def list_tools(self, owner_only: Optional[bool] = None) -> List[AgentTool]:
        """List all tools, optionally filtered by owner_only"""
        tools = list(self._tools.values())
        if owner_only is not None:
            tools = [t for t in tools if t.owner_only == owner_only]
        return tools

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions as JSON Schema"""
        return [tool.to_dict() for tool in self._tools.values()]


# Global tool registry instance
_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    """Get the global tool registry"""
    return _registry
