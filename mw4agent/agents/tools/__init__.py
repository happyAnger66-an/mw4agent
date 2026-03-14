"""Agent tools system"""

from .base import AgentTool, ToolResult
from .registry import ToolRegistry, get_tool_registry
from .gateway_tool import GatewayLsTool
from .read_tool import ReadTool
from .write_tool import WriteTool


def _register_builtin_tools() -> None:
    reg = get_tool_registry()
    for tool in (ReadTool(), WriteTool()):
        if reg.get_tool(tool.name) is None:
            reg.register(tool)


_register_builtin_tools()

__all__ = [
    "AgentTool",
    "ToolResult",
    "ToolRegistry",
    "get_tool_registry",
    "GatewayLsTool",
    "ReadTool",
    "WriteTool",
]
