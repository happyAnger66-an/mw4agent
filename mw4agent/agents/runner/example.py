"""Example usage of AgentRunner"""

import asyncio
from .runner import AgentRunner
from ..session.manager import SessionManager
from ..types import AgentRunParams
from ...tools.registry import get_tool_registry
from ...tools.examples import EchoTool, CalculatorTool


async def main():
    """Example: Run an agent with tools"""
    
    # Initialize components
    session_manager = SessionManager("example_sessions.json")
    runner = AgentRunner(session_manager)
    
    # Register tools
    registry = get_tool_registry()
    registry.register(EchoTool())
    registry.register(CalculatorTool())
    
    # Subscribe to events
    async def handle_event(event):
        print(f"[{event.stream}] {event.type}: {event.data}")
    
    runner.event_stream.subscribe("assistant", handle_event)
    runner.event_stream.subscribe("tool", handle_event)
    runner.event_stream.subscribe("lifecycle", handle_event)
    
    # Run agent
    params = AgentRunParams(
        message="Calculate 2 + 2",
        session_id="example_session",
        agent_id="main",
        model="gpt-4",
        provider="openai",
    )
    
    result = await runner.run(params)
    
    print(f"\nResult: {result.payloads[0].text}")
    print(f"Status: {result.meta.status}")
    print(f"Duration: {result.meta.duration_ms}ms")


if __name__ == "__main__":
    asyncio.run(main())
