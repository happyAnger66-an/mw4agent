"""
Agent Runner - core execution engine

Similar to OpenClaw's runEmbeddedPiAgent and pi-embedded-runner.
"""

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

from ..types import (
    AgentRunParams,
    AgentRunResult,
    AgentRunMeta,
    AgentRunStatus,
    AgentPayload,
    LifecyclePhase,
    StreamEvent,
)
from ..session.manager import SessionManager
from ..tools.registry import get_tool_registry
from ..queue.manager import CommandQueue
from ..events.stream import EventStream


class AgentRunner:
    """
    Agent Runner - executes agent turns
    
    Similar to OpenClaw's runEmbeddedPiAgent function.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        event_stream: Optional[EventStream] = None,
        queue: Optional[CommandQueue] = None,
    ):
        """
        Args:
            session_manager: Session manager instance
            event_stream: Event stream for emitting events
            queue: Command queue for serialization
        """
        self.session_manager = session_manager
        self.event_stream = event_stream or EventStream()
        self.queue = queue or CommandQueue()
        self.tool_registry = get_tool_registry()
        self._active_runs: Dict[str, Any] = {}

    async def run(
        self,
        params: AgentRunParams,
    ) -> AgentRunResult:
        """
        Run an agent turn
        
        Similar to runEmbeddedPiAgent in OpenClaw.
        
        Args:
            params: Agent run parameters
            
        Returns:
            AgentRunResult with payloads and metadata
        """
        run_id = str(uuid.uuid4())
        session_id = params.session_id or str(uuid.uuid4())
        session_key = params.session_key or f"agent:{params.agent_id or 'main'}:{session_id}"

        # Get or create session
        session_entry = self.session_manager.get_or_create_session(
            session_id=session_id,
            session_key=session_key,
            agent_id=params.agent_id,
        )

        # Emit lifecycle start event
        await self.event_stream.emit(
            StreamEvent(
                stream="lifecycle",
                type="start",
                data={
                    "run_id": run_id,
                    "session_id": session_id,
                    "session_key": session_key,
                },
            )
        )

        start_time = time.time()

        try:
            # Enqueue in command queue (serialize per session)
            async def execute_task():
                return await self._execute_agent_turn(params, run_id, session_entry)
            
            result = await self.queue.enqueue(
                session_key=session_key,
                run_id=run_id,
                task=execute_task,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            # Emit lifecycle end event
            await self.event_stream.emit(
                StreamEvent(
                    stream="lifecycle",
                    type="end",
                    data={
                        "run_id": run_id,
                        "session_id": session_id,
                        "status": "completed",
                    },
                )
            )

            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)

            # Emit lifecycle error event
            await self.event_stream.emit(
                StreamEvent(
                    stream="lifecycle",
                    type="error",
                    data={
                        "run_id": run_id,
                        "session_id": session_id,
                        "error": str(e),
                    },
                )
            )

            return AgentRunResult(
                payloads=[
                    AgentPayload(
                        text=f"Error: {str(e)}",
                        is_error=True,
                    )
                ],
                meta=AgentRunMeta(
                    duration_ms=duration_ms,
                    status=AgentRunStatus.ERROR,
                    error={"message": str(e)},
                ),
            )

    async def _execute_agent_turn(
        self,
        params: AgentRunParams,
        run_id: str,
        session_entry: Any,
    ) -> AgentRunResult:
        """
        Execute a single agent turn
        
        This is where the actual LLM interaction happens.
        Similar to runEmbeddedAttempt in OpenClaw.
        """
        # TODO: Implement actual LLM interaction
        # For now, return a placeholder result

        # Emit assistant stream events
        await self.event_stream.emit(
            StreamEvent(
                stream="assistant",
                type="delta",
                data={
                    "run_id": run_id,
                    "text": "Processing...",
                },
            )
        )

        # Simulate processing
        await asyncio.sleep(0.1)

        # Build response payload
        payload = AgentPayload(
            text=f"Agent response to: {params.message}",
        )

        # Update session
        self.session_manager.update_session(
            session_entry.session_id,
            message_count=session_entry.message_count + 1,
        )

        return AgentRunResult(
            payloads=[payload],
            meta=AgentRunMeta(
                duration_ms=100,
                status=AgentRunStatus.COMPLETED,
                provider=params.provider or "openai",
                model=params.model or "gpt-4",
            ),
        )

    async def execute_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Execute a tool call
        
        Similar to tool execution in OpenClaw's pi-embedded-runner.
        """
        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found")

        # Emit tool start event
        await self.event_stream.emit(
            StreamEvent(
                stream="tool",
                type="start",
                data={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "params": params,
                },
            )
        )

        try:
            # Execute tool
            result = await tool.execute(tool_call_id, params, context)

            # Emit tool end event
            await self.event_stream.emit(
                StreamEvent(
                    stream="tool",
                    type="end",
                    data={
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "success": result.success,
                        "result": result.result,
                    },
                )
            )

            return result

        except Exception as e:
            # Emit tool error event
            await self.event_stream.emit(
                StreamEvent(
                    stream="tool",
                    type="error",
                    data={
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "error": str(e),
                    },
                )
            )
            raise
