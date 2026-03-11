"""Base tool definitions"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ToolResult:
    """Tool execution result"""
    success: bool
    result: Any
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class AgentTool(ABC):
    """Base class for agent tools - similar to OpenClaw's AnyAgentTool"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Optional[Dict[str, Any]] = None,
        owner_only: bool = False,
    ):
        """
        Args:
            name: Tool name (must be unique)
            description: Tool description
            parameters: JSON Schema for parameters
            owner_only: Whether tool requires owner permission
        """
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.owner_only = owner_only

    @abstractmethod
    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """
        Execute the tool

        Args:
            tool_call_id: Unique ID for this tool call
            params: Tool parameters
            context: Execution context (session_key, agent_id, etc.)

        Returns:
            ToolResult with execution result
        """
        pass

    def to_dict(self) -> Dict[str, Any]:
        """Convert tool to dictionary (for JSON Schema)"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
