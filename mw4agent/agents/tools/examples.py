"""Example tools for demonstration"""

from typing import Any, Dict, Optional
from .base import AgentTool, ToolResult


class EchoTool(AgentTool):
    """Simple echo tool for testing"""

    def __init__(self):
        super().__init__(
            name="echo",
            description="Echo back the input text",
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to echo back",
                    }
                },
                "required": ["text"],
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        text = params.get("text", "")
        return ToolResult(success=True, result={"echo": text})


class CalculatorTool(AgentTool):
    """Simple calculator tool"""

    def __init__(self):
        super().__init__(
            name="calculator",
            description="Perform basic arithmetic operations",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression to evaluate (e.g., '2 + 2')",
                    }
                },
                "required": ["expression"],
            },
        )

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        expression = params.get("expression", "")
        try:
            # Simple eval for demo (in production, use a safe evaluator)
            result = eval(expression)
            return ToolResult(success=True, result={"expression": expression, "result": result})
        except Exception as e:
            return ToolResult(success=False, error=str(e))
