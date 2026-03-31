"""
Agent Runner - core execution engine

Similar to OpenClaw's runEmbeddedPiAgent and pi-embedded-runner.
"""

import asyncio
import os
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
from ..tools.base import ToolResult
from ..tools.policy import (
    filter_tools_by_policy,
    filter_tools_by_sandbox_policy,
    is_tool_allowed_by_sandbox,
    resolve_effective_allow_patterns,
    resolve_effective_policy_for_context,
    resolve_sandbox_tool_policy_config,
    resolve_tool_policy_config,
)
from ..tools.sandbox_workspace import ensure_sandbox_tool_workspace
from ..tools.timeout_defaults import resolve_default_tool_timeout_ms
from ..tools.fs_policy import resolve_tool_fs_policy_config
from ..queue.manager import CommandQueue
from ..events.stream import EventStream
from ...config import get_default_config_manager
from ...config.paths import resolve_agent_workspace_dir
from ...llm import generate_reply, generate_reply_with_tools, LLMUsage
from ..reasoning import split_reasoning_and_text
from ..skills.snapshot import build_skill_snapshot

from mw4agent.log import get_logger
logger = get_logger(__name__)

from ..session.transcript import (
    append_messages as append_transcript_messages,
    append_compaction,
    branch_to_parent,
    build_messages_from_leaf,
    drop_trailing_orphan_user,
    format_compaction_summary,
    get_leaf_entry_meta,
    limit_history_user_turns,
    read_messages as read_transcript_messages,
    resolve_history_limit_turns,
    split_by_user_turns,
)
from ..tools.web_search_tool import is_web_search_enabled

MAX_TOOL_ROUNDS = 30
TOOL_PROCESSING_START_SEC = 30.0
TOOL_PROCESSING_INTERVAL_SEC = 60.0


def _merge_llm_usage(a: LLMUsage, b: LLMUsage) -> LLMUsage:
    """Sum token counts across tool-loop rounds and the finalize text-only turn."""

    def _add(x: Optional[int], y: Optional[int]) -> Optional[int]:
        if x is None and y is None:
            return None
        return int((x or 0) + (y or 0))

    return LLMUsage(
        input_tokens=_add(a.input_tokens, b.input_tokens),
        output_tokens=_add(a.output_tokens, b.output_tokens),
        total_tokens=_add(a.total_tokens, b.total_tokens),
    )


def _resolve_run_workspace_dir(params: AgentRunParams) -> str:
    """Tools/memory/transcript cwd when params.workspace_dir is unset.

    Aligns with multi-agent layout: ~/.mw4agent/agents/<agentId>/workspace/
    (unless MW4AGENT_WORKSPACE_DIR globally overrides — see resolve_agent_workspace_dir).
    """
    wd = params.workspace_dir
    if wd is not None and str(wd).strip():
        return os.path.abspath(str(wd).strip())
    return resolve_agent_workspace_dir(params.agent_id)

_TOOL_NAME_ALIASES = {
    "bash": "exec",
    "shell_exec": "exec",
    "run_command": "exec",
    "apply-patch": "apply_patch",
}


def _normalize_tool_name(raw_name: str) -> str:
    """Normalize provider/legacy tool names to canonical registry names."""
    name = (raw_name or "").strip()
    if not name:
        return ""
    normalized_delimiter = name.replace("/", ".")
    parts = [p.strip() for p in normalized_delimiter.split(".") if p.strip()]
    if len(parts) >= 2 and parts[0].lower() in {"functions", "tools"}:
        normalized_delimiter = ".".join(parts[1:])
    normalized = normalized_delimiter.strip().lower()
    return _TOOL_NAME_ALIASES.get(normalized, normalized)


def _count_user_turns(messages: List[Dict[str, Any]]) -> int:
    return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")


def _read_session_compaction_cfg(root_cfg: Dict[str, Any]) -> Dict[str, Any]:
    session = root_cfg.get("session") if isinstance(root_cfg.get("session"), dict) else {}
    comp = session.get("compaction") if isinstance(session.get("compaction"), dict) else {}
    return comp


def _auto_compact_if_needed(
    *,
    history_messages: List[Dict[str, Any]],
    root_cfg: Dict[str, Any],
    transcript_file: str,
    transcript_session_id: str,
    transcript_cwd: str,
) -> List[Dict[str, Any]]:
    """Auto trigger compaction and rewrite leaf chain to: [compaction] + tail."""
    comp_cfg = _read_session_compaction_cfg(root_cfg)
    enabled = comp_cfg.get("enabled")
    if enabled is False:
        return history_messages
    # default enabled=true if config exists; otherwise disabled (no surprises)
    if enabled is None and not comp_cfg:
        return history_messages

    keep_turns = comp_cfg.get("keepTurns") or comp_cfg.get("keep_turns") or 12
    trigger_turns = comp_cfg.get("triggerTurns") or comp_cfg.get("trigger_turns") or 16
    summary_max_chars = comp_cfg.get("summaryMaxChars") or comp_cfg.get("summary_max_chars") or 4000

    try:
        keep_turns = int(keep_turns)
    except Exception:
        keep_turns = 12
    try:
        trigger_turns = int(trigger_turns)
    except Exception:
        trigger_turns = 16
    try:
        summary_max_chars = int(summary_max_chars)
    except Exception:
        summary_max_chars = 4000

    if keep_turns <= 0:
        keep_turns = 1
    if trigger_turns <= keep_turns:
        trigger_turns = keep_turns + 1

    user_turns = _count_user_turns(history_messages)
    if user_turns < trigger_turns:
        return history_messages

    older, keep = split_by_user_turns(history_messages, keep_last_user_turns=keep_turns)
    if not older or not keep:
        return history_messages

    # Avoid immediate re-compaction loops: if already starts with our marker, skip.
    first = keep[0] if keep else None
    if isinstance(first, dict) and first.get("role") == "system":
        c = str(first.get("content") or "")
        if "Session compaction summary (auto)" in c:
            return history_messages

    summary = format_compaction_summary(older, max_chars=summary_max_chars)
    # Reset leaf so the new chain starts from compaction (OpenClaw-like "replace older context").
    branch_to_parent(transcript_file=transcript_file, parent_id=None)
    compaction_id = append_compaction(
        transcript_file=transcript_file,
        session_id=transcript_session_id,
        cwd=transcript_cwd,
        summary=summary,
    )
    # Rewrite the recent tail so leaf-chain reconstruction includes it after compaction.
    append_transcript_messages(
        transcript_file=transcript_file,
        session_id=transcript_session_id,
        cwd=transcript_cwd,
        messages=keep,
    )
    logger.info(
        "auto compaction triggered: user_turns=%s keep_turns=%s trigger_turns=%s compaction_id=%s",
        user_turns,
        keep_turns,
        trigger_turns,
        compaction_id,
    )
    return build_messages_from_leaf(transcript_file=transcript_file)


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

    async def _emit_llm_response_message(
        self,
        run_id: str,
        params: AgentRunParams,
        *,
        phase: str,
        round_index: Optional[int],
        raw_content: Optional[str],
        tool_calls: Optional[List[Dict[str, Any]]],
        provider: str,
        model: str,
        usage: LLMUsage,
    ) -> None:
        """Emit structured LLM output on stream ``llm`` (thinking, visible text, tool plan)."""
        reasoning, text_only = split_reasoning_and_text(raw_content or "")
        tc_summary: Optional[List[Dict[str, str]]] = None
        if tool_calls:
            tc_summary = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                name = tc.get("name")
                args = tc.get("arguments")
                prev = ""
                if isinstance(args, dict):
                    prev = json.dumps(args, ensure_ascii=False)[:400]
                elif args is not None:
                    prev = str(args)[:400]
                tc_summary.append({"name": str(name or ""), "arguments_preview": prev})
            if not tc_summary:
                tc_summary = None
        usage_payload: Dict[str, int] = {}
        if isinstance(usage, LLMUsage):
            if usage.input_tokens is not None:
                usage_payload["input"] = int(usage.input_tokens)
            if usage.output_tokens is not None:
                usage_payload["output"] = int(usage.output_tokens)
            if usage.total_tokens is not None:
                usage_payload["total"] = int(usage.total_tokens)
        await self.event_stream.emit(
            StreamEvent(
                stream="llm",
                type="message",
                data={
                    "run_id": run_id,
                    "session_id": params.session_id,
                    "session_key": params.session_key,
                    "agent_id": params.agent_id,
                    "phase": phase,
                    "round": round_index,
                    "provider": provider,
                    "model": model,
                    "thinking": reasoning if reasoning.strip() else None,
                    "content": text_only if text_only.strip() else None,
                    "tool_calls": tc_summary,
                    "usage": usage_payload or None,
                },
            )
        )

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
                    "agent_id": params.agent_id,
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
                        "agent_id": params.agent_id,
                        "stop_reason": result.meta.stop_reason,
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
                        "agent_id": params.agent_id,
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

        cfg_mgr = get_default_config_manager()
        agent_workspace_dir = _resolve_run_workspace_dir(params)
        sandbox_policy = resolve_sandbox_tool_policy_config(cfg_mgr)
        run_sandbox = bool(params.sandbox is True)
        if run_sandbox and not sandbox_policy.enabled:
            sandbox_policy.enabled = True

        directory_isolation_active = False
        if sandbox_policy.should_isolate_directories(run_sandbox_request=run_sandbox):
            _, tool_workspace_dir = ensure_sandbox_tool_workspace(
                cfg_manager=cfg_mgr,
                agent_id=params.agent_id,
                session_id=str(session_entry.session_id),
            )
            directory_isolation_active = True
        else:
            tool_workspace_dir = agent_workspace_dir

        fs_policy = resolve_tool_fs_policy_config(cfg_mgr)
        tools_fs_workspace_only_effective = (
            fs_policy.workspace_only or directory_isolation_active
        )

        if (
            sandbox_policy.execution_isolation
            and str(sandbox_policy.execution_isolation).strip().lower() == "wasm"
        ):
            logger.info(
                "sandbox executionIsolation=wasm is set; WASM execution is not implemented yet — "
                "tools still run on the host under directory isolation rules"
            )

        # --- Attach skills snapshot to session & build prompt --------------
        # logger.info(f"Building skills snapshot for session {session_entry.session_id}")
        skills_snapshot = build_skill_snapshot(
            workspace_dir=agent_workspace_dir
        )
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

        logger.info(f"--> agent_turn start ")
        turn_stop_reason: Optional[str] = None
        if tool_plan is not None:
            tool_name = _normalize_tool_name(str(tool_plan["tool_name"]))
            logger.info(f"  ----> agent_turn tool_name: {tool_name}")
            tool_args = tool_plan.get("tool_args") or {}
            if not isinstance(tool_args, dict):
                tool_args = {}
            final_user_message = str(
                tool_plan.get("final_user_message") or params.message or ""
            )
            tool_call_id = str(tool_plan.get("tool_call_id") or uuid.uuid4())

            # For direct tool-call protocol runs, still honor tools policy so
            # implementations can relax internal guards (e.g. workspace root) in
            # profile=full.
            base_policy = resolve_tool_policy_config(cfg_mgr)
            effective_policy = resolve_effective_policy_for_context(
                cfg_mgr,
                base_policy=base_policy,
                channel=params.channel,
                user_id=params.sender_id,
                sender_is_owner=params.sender_is_owner,
                command_authorized=params.command_authorized,
            )

            tool_context = {
                "run_id": run_id,
                "session_key": params.session_key,
                "session_id": str(session_entry.session_id),
                "agent_id": params.agent_id,
                "workspace_dir": tool_workspace_dir,
                "agent_workspace_dir": agent_workspace_dir,
                "tools_profile": effective_policy.profile,
                "tools_allow": resolve_effective_allow_patterns(effective_policy),
                "tools_deny": effective_policy.deny,
                "sandbox_enabled": sandbox_policy.enabled,
                "sandbox_allow": sandbox_policy.allow,
                "sandbox_deny": sandbox_policy.deny,
                "sandbox_directory_isolation": directory_isolation_active,
                "sandbox_execution_isolation": sandbox_policy.execution_isolation,
                "tools_fs_workspace_only": tools_fs_workspace_only_effective,
                "default_tool_timeout_ms": resolve_default_tool_timeout_ms(),
            }
            if sandbox_policy.enabled and not is_tool_allowed_by_sandbox(
                sandbox_policy, tool_name
            ):
                raise ValueError(f"Tool '{tool_name}' blocked by sandbox policy")
            tool_result = await self.execute_tool(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                params=tool_args,
                context=tool_context,
            )
            logger.info(f"agent_turn tool_result: {tool_result}")

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
                composed_with_skills = skills_prompt + \
                    "\n\n[User]\n" + composed_message
            else:
                composed_with_skills = composed_message

            llm_params = replace(params_for_llm, message=composed_with_skills)
            await asyncio.sleep(0)
            reply_text, provider, model, usage = await asyncio.to_thread(generate_reply, llm_params)
            await self._emit_llm_response_message(
                run_id,
                params,
                phase="tool_plan",
                round_index=None,
                raw_content=reply_text,
                tool_calls=None,
                provider=provider,
                model=model,
                usage=usage,
            )
        else:
            # No tool plan → try tool-call loop if we have tools and non-echo provider.
            logger.info(f"  --> agent_turn no tool plan ")
            # --- Session short-term memory (transcript history) ----------------
            # Resolve transcript path from session store to keep store+transcripts colocated.
            # - MultiAgentSessionManager: ~/.mw4agent/agents/<agentId>/sessions/<sessionId>.jsonl
            # - SessionManager (--session-file): <dir(session_file)>/<sessionId>.jsonl
            try:
                transcript_file = self.session_manager.resolve_transcript_path(  # type: ignore[attr-defined]
                    session_entry.session_id, agent_id=params.agent_id
                )
            except TypeError:
                transcript_file = self.session_manager.resolve_transcript_path(  # type: ignore[attr-defined]
                    session_entry.session_id
                )
            # Prefer leaf-based reconstruction so branch/resetLeaf is respected.
            history_messages = build_messages_from_leaf(transcript_file=transcript_file)
            # If transcript leaf ends with an orphan user message (crash/interruption),
            # branch leaf back to parent so we don't generate invalid consecutive user turns.
            leaf_id, parent_id, leaf_msg = get_leaf_entry_meta(transcript_file=transcript_file)
            if isinstance(leaf_msg, dict) and leaf_msg.get("role") == "user" and parent_id:
                branch_to_parent(transcript_file=transcript_file, parent_id=parent_id)
                history_messages = build_messages_from_leaf(transcript_file=transcript_file)
            # Final safety: drop trailing orphan user in memory view.
            history_messages = drop_trailing_orphan_user(history_messages)
            history_messages = drop_trailing_orphan_user(history_messages)
            try:
                root_cfg = cfg_mgr.read_config("mw4agent", default={})
            except Exception:
                root_cfg = {}

            # Auto compaction trigger: compact older turns into a system summary entry,
            # then rewrite tail so leaf-based reconstruction sees summary + recent context.
            history_messages = _auto_compact_if_needed(
                history_messages=history_messages,
                root_cfg=root_cfg if isinstance(root_cfg, dict) else {},
                transcript_file=transcript_file,
                transcript_session_id=session_entry.session_id,
                transcript_cwd=agent_workspace_dir,
            )

            history_limit = resolve_history_limit_turns(
                cfg=root_cfg if isinstance(root_cfg, dict) else {},
                session_key=params.session_key,
            )
            history_messages = limit_history_user_turns(history_messages, history_limit)

            base_policy = resolve_tool_policy_config(cfg_mgr)
            effective_policy = resolve_effective_policy_for_context(
                cfg_mgr,
                base_policy=base_policy,
                channel=params.channel,
                user_id=params.sender_id,
                sender_is_owner=params.sender_is_owner,
                command_authorized=params.command_authorized,
            )
            all_tools = self.tool_registry.list_tools()
            tools_after_policy = filter_tools_by_policy(all_tools, effective_policy)
            # Enforce owner_only at runtime: non-owner callers看不到 owner_only 工具
            if not params.sender_is_owner:
                tools_after_policy = [t for t in tools_after_policy if not t.owner_only]
            # Do not expose web_search unless explicitly enabled (avoids unexpected external calls).
            if not is_web_search_enabled():
                tools_after_policy = [t for t in tools_after_policy if t.name != "web_search"]
            # Sandbox tool policy (optional) sits on top of normal policy.
            tools_after_policy = filter_tools_by_sandbox_policy(tools_after_policy, sandbox_policy)

            tool_definitions = [t.to_dict() for t in tools_after_policy]

            tool_context = {
                "run_id": run_id,
                "session_key": params.session_key,
                "session_id": str(session_entry.session_id),
                "agent_id": params.agent_id,
                "workspace_dir": tool_workspace_dir,
                "agent_workspace_dir": agent_workspace_dir,
                "channel": params.channel,
                "sender_id": params.sender_id,
                "sender_is_owner": params.sender_is_owner,
                "command_authorized": params.command_authorized,
                "tools_profile": effective_policy.profile,
                "tools_allow": resolve_effective_allow_patterns(effective_policy),
                "tools_deny": effective_policy.deny,
                "sandbox_enabled": sandbox_policy.enabled,
                "sandbox_allow": sandbox_policy.allow,
                "sandbox_deny": sandbox_policy.deny,
                "sandbox_directory_isolation": directory_isolation_active,
                "sandbox_execution_isolation": sandbox_policy.execution_isolation,
                "tools_fs_workspace_only": tools_fs_workspace_only_effective,
                "default_tool_timeout_ms": resolve_default_tool_timeout_ms(),
            }
            use_tool_loop = bool(tool_definitions)
            if use_tool_loop:
                logger.info(
                    f"  --> llm return use_tool_loop: {use_tool_loop}, tool_context: {tool_context}")
                reply_text, provider, model, usage, turn_stop_reason = await self._run_tool_loop(
                    params_for_llm,
                    tool_definitions,
                    tool_context,
                    run_id,
                    history_messages=history_messages,
                    transcript_file=transcript_file,
                    transcript_session_id=session_entry.session_id,
                    transcript_cwd=agent_workspace_dir,
                )
            else:
                logger.info(f"  --> llm return no tool plan ")
                await asyncio.sleep(0)
                messages: List[Dict[str, Any]] = []
                if params_for_llm.extra_system_prompt:
                    messages.append(
                        {"role": "system", "content": params_for_llm.extra_system_prompt.strip()}
                    )
                messages.extend(history_messages)
                user_msg = {"role": "user", "content": params_for_llm.message or ""}
                messages.append(user_msg)

                reply_text, provider, model, usage = await asyncio.to_thread(
                    generate_reply, params_for_llm, messages=messages
                )
                await self._emit_llm_response_message(
                    run_id,
                    params,
                    phase="single_turn",
                    round_index=None,
                    raw_content=reply_text,
                    tool_calls=None,
                    provider=provider,
                    model=model,
                    usage=usage,
                )

                # Persist transcript: user + assistant.
                append_transcript_messages(
                    transcript_file=transcript_file,
                    session_id=session_entry.session_id,
                    cwd=agent_workspace_dir,
                    messages=[user_msg, {"role": "assistant", "content": reply_text or ""}],
                )

        # Emit assistant event(s): optionally reasoning then text (ReasoningLevel).
        reasoning_level = (
            params.reasoning_level or "").strip().lower() or "off"
        reasoning, text_only = split_reasoning_and_text(reply_text or "")
        if reasoning_level in ("on", "stream") and reasoning:
            await self.event_stream.emit(
                StreamEvent(
                    stream="assistant",
                    type="delta",
                    data={
                        "run_id": run_id,
                        "reasoning": reasoning,
                        "final": False,
                    },
                )
            )
        await self.event_stream.emit(
            StreamEvent(
                stream="assistant",
                type="delta",
                data={
                    "run_id": run_id,
                    "text": text_only,
                    "final": True,
                },
            )
        )

        logger.info(f"agent_turn session_id: {session_entry.session_id}") 
        # Update session metadata.
        try:
            # Multi-agent session managers may require agent scoping for updates.
            self.session_manager.update_session(  # type: ignore[call-arg]
                session_entry.session_id,
                agent_id=getattr(session_entry, "agent_id", None),
                message_count=session_entry.message_count + 1,
            )
        except TypeError:
            # Back-compat: single-store session managers.
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

        payload = AgentPayload(text=text_only)

        return AgentRunResult(
            payloads=[payload],
            meta=AgentRunMeta(
                duration_ms=duration_ms,
                status=AgentRunStatus.COMPLETED,
                provider=provider,
                model=model,
                usage=usage_dict,
                stop_reason=turn_stop_reason,
            ),
        )

    async def _run_tool_loop(
        self,
        params: AgentRunParams,
        tool_definitions: List[Dict[str, Any]],
        tool_context: Dict[str, Any],
        run_id: str,
        *,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        transcript_file: Optional[str] = None,
        transcript_session_id: Optional[str] = None,
        transcript_cwd: str = "",
    ) -> tuple:
        # Returns (reply_text, provider, model, usage, tool_loop_stop_reason)
        """Run LLM with tools in a loop until no tool_calls or max rounds.

        If the loop exits because the last allowed round still requested tools,
        runs one final text-only LLM turn and sets ``tool_loop_stop_reason`` to
        ``\"max_tool_rounds\"``.
        """
        logger.info(f"    ----> run_tool_loop tool_loop start: {tool_context}")
        messages: List[Dict[str, Any]] = []
        if params.extra_system_prompt:
            messages.append({
                "role": "system",
                "content": params.extra_system_prompt.strip(),
            })
        if history_messages:
            messages.extend(history_messages)
        user_msg = {"role": "user", "content": params.message or ""}
        messages.append(user_msg)
        if transcript_file and transcript_session_id:
            append_transcript_messages(
                transcript_file=transcript_file,
                session_id=transcript_session_id,
                cwd=transcript_cwd,
                messages=[user_msg],
            )

        reply_text = ""
        provider = "echo"
        model = ""
        usage = LLMUsage()
        tool_loop_stop_reason: Optional[str] = None
        for round_idx in range(MAX_TOOL_ROUNDS):
            await asyncio.sleep(0)
            content, tool_calls, provider, model, usage = await asyncio.to_thread(
                generate_reply_with_tools, params, messages, tool_definitions
            )
            logger.info(
                "      ----> run_tool_loop %s tool_calls: %s content: %s",
                round_idx,
                tool_calls,
                content,
            )
            await self._emit_llm_response_message(
                run_id,
                params,
                phase="tool_loop",
                round_index=round_idx,
                raw_content=content,
                tool_calls=tool_calls if tool_calls else None,
                provider=provider,
                model=model,
                usage=usage,
            )
            if not tool_calls:
                reply_text = content or ""
                if transcript_file and transcript_session_id:
                    append_transcript_messages(
                        transcript_file=transcript_file,
                        session_id=transcript_session_id,
                        cwd=transcript_cwd,
                        messages=[{"role": "assistant", "content": reply_text}],
                    )
                break
            normalized_tool_calls = [
                {
                    "id": tc["id"],
                    "name": _normalize_tool_name(str(tc["name"])),
                    "arguments": tc["arguments"],
                }
                for tc in tool_calls
            ]
            logger.info(
                "executing %d tool call(s): %s",
                len(normalized_tool_calls),
                [(tc.get("name"), tc.get("arguments")) for tc in normalized_tool_calls],
            )
            # Emit tool start/end for each call and collect results.
            assistant_msg = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in normalized_tool_calls
                ],
            }
            messages.append(assistant_msg)
            if transcript_file and transcript_session_id:
                append_transcript_messages(
                    transcript_file=transcript_file,
                    session_id=transcript_session_id,
                    cwd=transcript_cwd,
                    messages=[assistant_msg],
                )
            for tc in normalized_tool_calls:
                tid, name, args = tc["id"], tc["name"], tc["arguments"]
                try:
                    result = await self.execute_tool(
                        tool_call_id=tid,
                        tool_name=name,
                        params=args,
                        context=tool_context,
                    )
                except Exception as e:
                    result = ToolResult(success=False, result={}, error=str(e))
                logger.info(
                    "tool %s result: success=%s %s",
                    name,
                    result.success,
                    (str(result.result)[
                     :120] if result.success else result.error or "")[:120],
                )
                if result.success:
                    result_str = json.dumps(result.result, ensure_ascii=False) if isinstance(
                        result.result, dict) else str(result.result)
                else:
                    result_str = f"Error: {result.error or result.result}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tid,
                    "content": result_str,
                })
                if transcript_file and transcript_session_id:
                    append_transcript_messages(
                        transcript_file=transcript_file,
                        session_id=transcript_session_id,
                        cwd=transcript_cwd,
                        messages=[{"role": "tool", "tool_call_id": tid, "content": result_str}],
                    )
            if round_idx == MAX_TOOL_ROUNDS - 1:
                tool_loop_stop_reason = "max_tool_rounds"

        if tool_loop_stop_reason == "max_tool_rounds":
            cap_note = (
                f"[System] The tool-call loop reached its safety limit ({MAX_TOOL_ROUNDS} rounds). "
                "Summarize what was accomplished, what is still incomplete, and suggest concrete next steps. "
                "Use the same language as the user when possible. Do not propose further tool calls."
            )
            messages.append({"role": "system", "content": cap_note})
            final_text, provider2, model2, usage2 = await asyncio.to_thread(
                generate_reply, params, messages=messages
            )
            reply_text = (final_text or "").strip()
            provider, model = provider2, model2
            usage = _merge_llm_usage(usage, usage2)
            await self._emit_llm_response_message(
                run_id,
                params,
                phase="tool_loop_finalize",
                round_index=None,
                raw_content=reply_text,
                tool_calls=None,
                provider=provider2,
                model=model2,
                usage=usage2,
            )
            if transcript_file and transcript_session_id:
                append_transcript_messages(
                    transcript_file=transcript_file,
                    session_id=transcript_session_id,
                    cwd=transcript_cwd,
                    messages=[{"role": "assistant", "content": reply_text}],
                )

        return (reply_text, provider, model, usage, tool_loop_stop_reason)

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
        normalized_tool_name = _normalize_tool_name(tool_name)
        tool = self.tool_registry.get_tool(normalized_tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found")

        # Defense in depth: enforce sandbox policy at execution time too.
        try:
            sandbox_enabled = bool((context or {}).get("sandbox_enabled") is True)
            if sandbox_enabled:
                from ..tools.policy import SandboxToolPolicy, is_tool_allowed_by_sandbox

                policy = SandboxToolPolicy(
                    enabled=True,
                    allow=(context or {}).get("sandbox_allow"),
                    deny=(context or {}).get("sandbox_deny"),
                )
                if not is_tool_allowed_by_sandbox(policy, normalized_tool_name):
                    raise ValueError(
                        f"Tool '{normalized_tool_name}' blocked by sandbox policy"
                    )
        except Exception:
            # If sandbox context is malformed, fail closed when sandbox_enabled is set.
            if bool((context or {}).get("sandbox_enabled") is True):
                raise

        run_id_for_stream = (context or {}).get("run_id")
        started_at_ms = int(time.time() * 1000)

        # Emit tool start event
        await self.event_stream.emit(
            StreamEvent(
                stream="tool",
                type="start",
                data={
                    "run_id": run_id_for_stream,
                    "tool_call_id": tool_call_id,
                    "tool_name": normalized_tool_name,
                    "params": params,
                },
            )
        )

        async def _emit_tool_processing() -> None:
            # After a grace period (default 30s), emit periodic processing events (default every 60s).
            await asyncio.sleep(max(0.0, float(TOOL_PROCESSING_START_SEC)))
            while True:
                now_ms = int(time.time() * 1000)
                elapsed_ms = max(0, now_ms - started_at_ms)
                await self.event_stream.emit(
                    StreamEvent(
                        stream="tool",
                        type="processing",
                        data={
                            "run_id": run_id_for_stream,
                            "tool_call_id": tool_call_id,
                            "tool_name": normalized_tool_name,
                            "elapsed_ms": elapsed_ms,
                        },
                    )
                )
                await asyncio.sleep(max(0.0, float(TOOL_PROCESSING_INTERVAL_SEC)))

        processing_task = asyncio.create_task(_emit_tool_processing())
        try:
            # Execute tool
            result = await tool.execute(tool_call_id, params, context)

            # Emit tool end event
            await self.event_stream.emit(
                StreamEvent(
                    stream="tool",
                    type="end",
                    data={
                        "run_id": run_id_for_stream,
                        "tool_call_id": tool_call_id,
                        "tool_name": normalized_tool_name,
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
                        "run_id": run_id_for_stream,
                        "tool_call_id": tool_call_id,
                        "tool_name": normalized_tool_name,
                        "error": str(e),
                    },
                )
            )
            raise
        finally:
            processing_task.cancel()
            try:
                await processing_task
            except asyncio.CancelledError:
                pass
