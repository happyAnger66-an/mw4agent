"""Echo tool for plugin loader tests."""

from mw4agent.agents.tools.base import AgentTool, ToolResult


class EchoTool(AgentTool):
    def __init__(self):
        super().__init__(
            name="echo",
            description="Echo back the message.",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )

    async def execute(self, tool_call_id, params, context=None):
        msg = (params or {}).get("message", "")
        return ToolResult(success=True, result={"echo": msg})


def register_tools(registry=None):
    if registry is None:
        from mw4agent.agents.tools import get_tool_registry
        registry = get_tool_registry()
    registry.register(EchoTool())
