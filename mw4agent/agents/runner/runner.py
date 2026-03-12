"""
Agent Runner - core execution engine

Similar to OpenClaw's runEmbeddedPiAgent and pi-embedded-runner.
"""

import asyncio
import time
import uuid
import json
from dataclasses import replace
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
from ...llm import generate_reply, LLMUsage
from ..skills.snapshot import build_skill_snapshot


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
        run_id = params.run_id or str(uuid.uuid4())
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
        Execute a single agent turn.

        This is where the actual LLM interaction happens.
        Similar in spirit to OpenClaw's runEmbeddedAttempt (single run attempt).
        """
        # Emit a lightweight "processing" delta for streaming UIs.
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

        started = time.time()

        # --- Attach skills snapshot to session & build prompt --------------
        skills_snapshot = build_skill_snapshot()
        skills_prompt = ''
        if skills_snapshot.get("prompt"):
            skills_prompt = str(skills_snapshot["prompt"])
            # Attach snapshot to session metadata (best-effort).
            try:
                meta = getattr(session_entry, "metadata", None) or {}
                meta = dict(meta)
                meta["skills_snapshot"] = skills_snapshot
                session_entry.metadata = meta  # type: ignore[attr-defined]
            except Exception:
                # Non-critical; do not break the run if anything goes wrong.
                pass

        base_message = params.message or ""
        if skills_prompt:
            composed_for_llm = skills_prompt + "\n\n[User]\n" + base_message
            params_for_llm = replace(params, message=composed_for_llm)
        else:
            params_for_llm = params

        # --- Minimal tool-call protocol ------------------------------------
        #
        # If params.message 是一个 JSON 且形如：
        # {
        #   "type": "tool_call",
        #   "tool_name": "gateway_ls",
        #   "tool_args": {"path": "."},
        #   "final_user_message": "请根据文件列表给出下一步建议"
        # }
        #
        # 则：
        #   1) 先调用对应工具（通过 ToolRegistry）
        #   2) 将工具结果拼入一个新的 prompt，再调用 LLM 生成最终回答。
        #
        # 其他情况：退化为单次 LLM 调用（兼容现有行为）。
        tool_plan: Optional[Dict[str, Any]] = None
        try:
            maybe_json = json.loads(params.message)
            if isinstance(maybe_json, dict) and maybe_json.get("type") == "tool_call":
                if isinstance(maybe_json.get("tool_name"), str):
                    tool_plan = maybe_json
        except Exception:
            tool_plan = None

        if tool_plan is not None:
            tool_name = str(tool_plan["tool_name"])
            tool_args = tool_plan.get("tool_args") or {}
            if not isinstance(tool_args, dict):
                tool_args = {}
            final_user_message = str(
                tool_plan.get("final_user_message") or params.message or ""
            )
            tool_call_id = str(tool_plan.get("tool_call_id") or uuid.uuid4())

            # Execute tool via shared helper (emits tool stream events).
            tool_result = await self.execute_tool(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                params=tool_args,
                context={
                    "run_id": run_id,
                    "session_key": params.session_key,
                    "agent_id": params.agent_id,
                },
            )

            if getattr(tool_result, "success", False):
                tool_text = f"Tool {tool_name} succeeded with result:\n{tool_result.result!r}"
            else:
                tool_text = (
                    f"Tool {tool_name} failed with error: "
                    f"{getattr(tool_result, 'error', None) or getattr(tool_result, 'result', None)!r}"
                )

            composed_message = (
                final_user_message
                + "\n\n[Tool "
                + tool_name
                + " output]\n"
                + tool_text
            )

            if skills_prompt:
                composed_with_skills = skills_prompt + "\n\n[User]\n" + composed_message
            else:
                composed_with_skills = composed_message

            llm_params = replace(params_for_llm, message=composed_with_skills)
            reply_text, provider, model, usage = generate_reply(llm_params)
        else:
            # No tool plan → single-shot LLM call.
            reply_text, provider, model, usage = generate_reply(params_for_llm)

        # Emit final assistant event.
        await self.event_stream.emit(
            StreamEvent(
                stream="assistant",
                type="delta",
                data={
                    "run_id": run_id,
                    "text": reply_text,
                    "final": True,
                },
            )
        )

        # Update session metadata.
        self.session_manager.update_session(
            session_entry.session_id,
            message_count=session_entry.message_count + 1,
        )

        duration_ms = int((time.time() - started) * 1000)

        usage_dict: Optional[Dict[str, int]] = None
        if isinstance(usage, LLMUsage) and any(
            v is not None for v in (usage.input_tokens, usage.output_tokens, usage.total_tokens)
        ):
            usage_dict = {}
            if usage.input_tokens is not None:
                usage_dict["input"] = int(usage.input_tokens)
            if usage.output_tokens is not None:
                usage_dict["output"] = int(usage.output_tokens)
            if usage.total_tokens is not None:
                usage_dict["total"] = int(usage.total_tokens)

        payload = AgentPayload(text=reply_text)

        return AgentRunResult(
            payloads=[payload],
            meta=AgentRunMeta(
                duration_ms=duration_ms,
                status=AgentRunStatus.COMPLETED,
                provider=provider,
                model=model,
                usage=usage_dict,
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
