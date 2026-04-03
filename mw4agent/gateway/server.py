"""FastAPI Gateway server (OpenClaw-inspired).

Endpoints:
- POST /rpc: JSON RPC-ish {id, method, params}
- GET /health
- WS  /ws: agent events stream
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..agents.runner.runner import AgentRunner
from ..agents.session import MultiAgentSessionManager, SessionManager
from ..agents.types import AgentRunParams
from ..agents.agent_manager import AgentManager
from ..log import get_logger
from ..memory.bootstrap import load_bootstrap_system_prompt
from ..plugin import load_plugins
from .state import DedupeEntry, GatewayState, RunSnapshot
from .types import AgentEvent
from .wait_timeout import resolve_agent_wait_timeout_ms
from .orchestrator import Orchestrator

logger = get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)

def _parse_reset_command(message: str) -> tuple[bool, str]:
    """Return (reset_triggered, remaining_message)."""
    raw = (message or "").strip()
    if not raw:
        return (False, "")
    lowered = raw.lower()
    for cmd in ("/reset", "/new"):
        if lowered == cmd:
            return (True, "")
        if lowered.startswith(cmd + " "):
            return (True, raw[len(cmd) :].strip())
    return (False, raw)

def _is_safe_rel_path(path: str) -> bool:
    # Minimal safety guard for the demo ls RPC:
    # - disallow absolute paths
    # - disallow parent traversal
    # - disallow NUL
    if "\x00" in path:
        return False
    if path.startswith(("/", "\\")):
        return False
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if any(p == ".." for p in parts):
        return False
    return True


def _resolve_agent_workspace_file_abs(
    *,
    agent_manager: AgentManager,
    agent_id: str,
    rel_path: str,
    prefer_existing_case_variant: bool = True,
) -> tuple[str, str]:
    """Resolve an allowed file under this agent's workspace dir.

    Returns (normalized_rel_path, abs_path).

    - Only allows a small set of root bootstrap files for the web editor UI.
    - Optionally prefers an existing case-variant (e.g. request "memory.md" but "MEMORY.md" exists).
    - Guards against path traversal by checking resolved path is under workspace root.
    """
    rel = (rel_path or "").strip().lstrip("/").replace("\\", "/")
    allowed = {"memory.md", "MEMORY.md", "SOUL.md", "soul.md"}
    if rel not in allowed:
        raise ValueError(f"unsupported file: {rel!r}")

    candidates = [rel]
    if prefer_existing_case_variant:
        if rel in {"memory.md", "MEMORY.md"}:
            candidates = ["MEMORY.md", "memory.md"]
        elif rel in {"SOUL.md", "soul.md"}:
            candidates = ["SOUL.md", "soul.md"]

    workspace_dir = agent_manager.get_or_create(agent_id).workspace_dir
    root = Path(workspace_dir).resolve()

    # Pick the first existing candidate; otherwise use the first candidate as default target.
    chosen = candidates[0]
    for c in candidates:
        p = (root / c).resolve()
        if root != p and root not in p.parents:
            continue
        if p.is_file():
            chosen = c
            break

    target = (root / chosen).resolve()
    if root != target and root not in target.parents:
        raise ValueError("refusing to access path outside workspace")
    return chosen, str(target)


def _normalize_agent_id_for_runs(agent_id: Optional[str]) -> str:
    from ..config.paths import normalize_agent_id

    return normalize_agent_id(agent_id)


def _run_status_for_agent(state: GatewayState, agent_id: str) -> Dict[str, Any]:
    """Summarize Gateway run records scoped to an agent (for dashboard)."""
    target = _normalize_agent_id_for_runs(agent_id)
    running = 0
    last_snap: Optional[RunSnapshot] = None
    last_ended = -1
    for rec in state.runs.values():
        ra = rec.agent_id
        if ra is None or not str(ra).strip():
            continue
        if _normalize_agent_id_for_runs(ra) != target:
            continue
        if not rec.done.is_set():
            running += 1
        snap = rec.snapshot
        if snap and snap.ended_at is not None and int(snap.ended_at) > last_ended:
            last_ended = int(snap.ended_at)
            last_snap = snap
    out: Dict[str, Any] = {
        "state": "running" if running > 0 else "idle",
        "activeRuns": running,
        "lastRun": None,
    }
    if last_snap:
        out["lastRun"] = {
            "runId": last_snap.run_id,
            "status": last_snap.status,
            "startedAt": last_snap.started_at,
            "endedAt": last_snap.ended_at,
            "error": last_snap.error,
        }
    return out


def create_app(
    *,
    session_file: str = "",
    node_token: Optional[str] = None,
) -> FastAPI:
    # Load plugins (register tools from MW4AGENT_PLUGIN_DIR) before creating runner
    load_plugins()

    if node_token is None:
        t = os.environ.get("GATEWAY_NODE_TOKEN")
        node_token = t.strip() if isinstance(t, str) and t.strip() else None
    else:
        node_token = node_token.strip() if isinstance(node_token, str) and node_token.strip() else None
    state = GatewayState(node_token=node_token)
    agent_manager = AgentManager()
    # Back-compat: if a session_file is provided, use a single store.
    # Default: multi-agent stores under ~/.mw4agent/agents/<agentId>/sessions/sessions.json
    if session_file and session_file.strip():
        session_manager = SessionManager(session_file.strip())
    else:
        session_manager = MultiAgentSessionManager(agent_manager=agent_manager)
    runner = AgentRunner(session_manager)
    orchestrator = Orchestrator(agent_manager=agent_manager, runner=runner)
    try:
        n_stale = orchestrator.reconcile_stale_running_states()
        if n_stale > 0:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "reconciled %s orchestration(s) stuck as running after gateway restart",
                n_stale,
            )
    except Exception:
        import logging as _logging

        _logging.getLogger(__name__).exception("orchestrator reconcile_stale_running_states failed")

    # --- Feishu：按 channels.feishu 解析出的全部账号自动注册；webhook 挂载多路由，websocket 在 lifespan 内各启一条连接 ---
    feishu_webhook_routers: List[Any] = []
    feishu_ws_plugins: List[Any] = []
    feishu_ws_dispatcher: Optional[Any] = None
    try:
        from ..config import read_root_section
        from ..channels.dispatcher import ChannelDispatcher, ChannelRuntime
        from ..channels.feishu_accounts import list_feishu_accounts
        from ..channels.plugins.feishu import FeishuChannel
        from ..channels.registry import ChannelRegistry

        channels = read_root_section("channels", default={})
        feishu_section = channels.get("feishu") or {}
        env_id = os.getenv("FEISHU_APP_ID", "").strip()
        env_sec = os.getenv("FEISHU_APP_SECRET", "").strip()
        feishu_accounts = list_feishu_accounts(feishu_section, env_app_id=env_id, env_app_secret=env_sec)
        if feishu_accounts:
            registry = ChannelRegistry()
            feishu_plugins: List[Any] = []
            for acc in feishu_accounts:
                feishu_plugin = FeishuChannel(feishu_account=acc)
                registry.register_plugin(feishu_plugin)
                feishu_plugins.append(feishu_plugin)
            runtime = ChannelRuntime(
                session_manager=session_manager,
                agent_runner=runner,
                gateway_base_url=None,
            )
            dispatcher = ChannelDispatcher(runtime=runtime, registry=registry)
            feishu_ws_dispatcher = dispatcher
            for p in feishu_plugins:
                if p.connection_mode == "webhook":
                    feishu_webhook_routers.append(
                        p.get_webhook_router(on_inbound=dispatcher.dispatch_inbound)
                    )
                    logger.info(
                        "Feishu channel enabled (webhook): account=%s path=%s agent=%s",
                        p._account_key,
                        p.path,
                        p._default_agent_id,
                    )
                else:
                    feishu_ws_plugins.append(p)
                    logger.info(
                        "Feishu channel enabled (websocket, lifespan): account=%s agent=%s",
                        p._account_key,
                        p._default_agent_id,
                    )
    except Exception as e:
        logger.debug("Feishu channel not started: %s", e)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if feishu_ws_dispatcher is not None:
            disp = feishu_ws_dispatcher
            for plugin in feishu_ws_plugins:
                asyncio.create_task(plugin._run_ws_monitor(on_inbound=disp.dispatch_inbound))
        yield

    app = FastAPI(title="MW4Agent Gateway", version="0.1", lifespan=lifespan)
    # Allow the Next.js / Tauri desktop UI (and other local dev origins) to call POST /rpc.
    # Set GATEWAY_CORS_ORIGINS="http://localhost:3000,https://tauri.localhost" for stricter dev,
    # or leave unset for allow_origins=["*"] (no credentials).
    _cors_raw = (os.environ.get("GATEWAY_CORS_ORIGINS") or "").strip()
    if _cors_raw:
        _cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    else:
        _cors_origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in feishu_webhook_routers:
        app.include_router(r)

    # --- Bridge AgentRunner events -> Gateway WS broadcasts + run snapshots ---
    async def handle_agent_stream_event(evt) -> None:
        # evt can be mw4agent.agents.events.stream.StreamEvent (our internal)
        run_id = str(evt.data.get("run_id") or "")
        if not run_id:
            return

        aid = evt.data.get("agent_id")
        agent_id_ev = str(aid).strip() if aid is not None and str(aid).strip() else None
        rec = state.ensure_run(
            run_id=run_id,
            session_key=str(evt.data.get("session_key") or ""),
            agent_id=agent_id_ev,
        )
        rec.seq += 1

        if evt.stream == "lifecycle":
            phase = evt.type
            if phase == "start":
                started_at = evt.data.get("startedAt", _now_ms())
                rec.started_at_ms = int(started_at) if started_at is not None else _now_ms()
                await state.broadcast(
                    AgentEvent(
                        run_id=run_id,
                        stream="lifecycle",
                        data={"phase": "start", "startedAt": started_at},
                        seq=rec.seq,
                    )
                )
                return
            if phase == "end":
                ended_at = evt.data.get("endedAt", _now_ms())
                await state.broadcast(
                    AgentEvent(
                        run_id=run_id,
                        stream="lifecycle",
                        data={"phase": "end", "endedAt": ended_at},
                        seq=rec.seq,
                    )
                )
                sr = evt.data.get("stop_reason")
                state.mark_run_terminal(
                    run_id,
                    RunSnapshot(
                        run_id=run_id,
                        status="ok",
                        started_at=rec.started_at_ms,
                        ended_at=int(ended_at),
                        reply_text=rec.reply_text_buffer.strip() if rec.reply_text_buffer else None,
                        stop_reason=str(sr).strip() if sr else None,
                    ),
                )
                return
            if phase == "error":
                ended_at = evt.data.get("endedAt", _now_ms())
                error = str(evt.data.get("error") or "error")
                await state.broadcast(
                    AgentEvent(
                        run_id=run_id,
                        stream="lifecycle",
                        data={"phase": "error", "endedAt": ended_at, "error": error},
                        seq=rec.seq,
                    )
                )
                state.mark_run_terminal(
                    run_id,
                    RunSnapshot(
                        run_id=run_id,
                        status="error",
                        started_at=rec.started_at_ms,
                        ended_at=int(ended_at),
                        error=error,
                        reply_text=rec.reply_text_buffer.strip() if rec.reply_text_buffer else None,
                        stop_reason=None,
                    ),
                )
                return

        if evt.stream == "assistant":
            # Accumulate assistant reply text
            text = evt.data.get("text") or evt.data.get("delta") or ""
            if text and isinstance(text, str):
                rec.reply_text_buffer += text
            await state.broadcast(
                AgentEvent(
                    run_id=run_id,
                    stream="assistant",
                    data={"type": evt.type, **evt.data},
                    seq=rec.seq,
                )
            )
            return

        if evt.stream == "tool":
            await state.broadcast(
                AgentEvent(
                    run_id=run_id,
                    stream="tool",
                    data={"type": evt.type, **evt.data},
                    seq=rec.seq,
                )
            )
            return

        if evt.stream == "llm":
            await state.broadcast(
                AgentEvent(
                    run_id=run_id,
                    stream="llm",
                    data={"type": evt.type, **evt.data},
                    seq=rec.seq,
                )
            )
            return

    runner.event_stream.subscribe("lifecycle", handle_agent_stream_event)
    runner.event_stream.subscribe("assistant", handle_agent_stream_event)
    runner.event_stream.subscribe("tool", handle_agent_stream_event)
    runner.event_stream.subscribe("llm", handle_agent_stream_event)

    # Expose in app.state for handlers
    app.state.gateway_state = state
    app.state.session_manager = session_manager
    app.state.agent_runner = runner

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"ok": True, "ts": _now_ms(), "runs": len(state.runs)}

    @app.websocket("/ws")
    async def ws_events(ws: WebSocket):
        await ws.accept()
        q, unregister = await state.register_ws()
        try:
            while True:
                evt: AgentEvent = await q.get()
                await ws.send_text(json.dumps(asdict(evt), ensure_ascii=False))
        except WebSocketDisconnect:
            unregister()
        except Exception:
            unregister()

    @app.websocket("/ws-node")
    async def ws_node(ws: WebSocket):
        """OpenClaw-compatible node connection: connect.challenge + connect (role=node) with auth."""
        await ws.accept()
        conn_id = str(uuid.uuid4())
        # Send connect.challenge so client can send connect with optional auth
        await ws.send_text(
            json.dumps(
                {
                    "type": "event",
                    "event": "connect.challenge",
                    "payload": {"nonce": conn_id, "ts": _now_ms()},
                },
                ensure_ascii=False,
            )
        )
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") != "req":
                    continue
                req_id = msg.get("id") or ""
                method = msg.get("method") or ""
                params = msg.get("params")
                if not isinstance(params, dict):
                    params = {}

                def send_res(ok: bool, payload: Any = None, error: Any = None) -> None:
                    asyncio.create_task(
                        _send_node_res(ws, req_id, ok, payload=payload, error=error)
                    )

                if method == "connect":
                    role = (params.get("role") or "").strip().lower()
                    if role != "node":
                        send_res(
                            False,
                            error={"code": "invalid_request", "message": "only role=node accepted on /ws-node"},
                        )
                        continue
                    # Node authentication: if state.node_token is set, require params.auth.token to match
                    if state.node_token is not None:
                        auth = params.get("auth")
                        token = auth.get("token") if isinstance(auth, dict) else None
                        if not isinstance(token, str) or token.strip() != state.node_token:
                            send_res(
                                False,
                                error={
                                    "code": "invalid_request",
                                    "message": "node authentication required (invalid or missing token)",
                                },
                            )
                            continue
                    client = params.get("client") or {}
                    node_id = (client.get("id") or "").strip() or conn_id
                    display_name = client.get("displayName") if isinstance(client.get("displayName"), str) else None
                    platform_name = client.get("platform") if isinstance(client.get("platform"), str) else None
                    caps = params.get("caps")
                    commands = params.get("commands")
                    if not isinstance(caps, list):
                        caps = []
                    if not isinstance(commands, list):
                        commands = []
                    state.node_registry.register(
                        ws,
                        node_id=node_id,
                        conn_id=conn_id,
                        display_name=display_name,
                        platform=platform_name,
                        caps=caps,
                        commands=commands,
                    )
                    hello_ok = {
                        "type": "hello-ok",
                        "protocol": 1,
                        "server": {"version": "0.1", "connId": conn_id},
                        "features": {"methods": ["node.list", "node.invoke"], "events": ["node.invoke.request"]},
                        "snapshot": {},
                    }
                    send_res(True, payload=hello_ok)
                    continue

                if method == "node.invoke.result":
                    result_id = params.get("id") or ""
                    result_node_id = params.get("nodeId") or ""
                    ok = params.get("ok") is True
                    state.node_registry.handle_invoke_result(
                        request_id=result_id,
                        node_id=result_node_id,
                        ok=ok,
                        payload=params.get("payload"),
                        payload_json=params.get("payloadJSON"),
                        error=params.get("error"),
                    )
                    send_res(True, payload={})
                    continue

                send_res(False, error={"code": "method_not_found", "message": f"Unknown method: {method}"})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            state.node_registry.unregister(conn_id)

    async def _send_node_res(ws: WebSocket, req_id: str, ok: bool, payload: Any = None, error: Any = None) -> None:
        try:
            await ws.send_text(
                json.dumps(
                    {"type": "res", "id": req_id, "ok": ok, "payload": payload, "error": error},
                    ensure_ascii=False,
                )
            )
        except Exception:
            pass

    @app.post("/rpc")
    async def rpc(body: Dict[str, Any]):
        req_id = str(body.get("id") or "")
        method = str(body.get("method") or "")
        params = body.get("params")
        if not isinstance(params, dict):
            params = {}

        if not req_id or not method:
            return JSONResponse(
                status_code=400,
                content={"id": req_id or None, "ok": False, "error": {"code": "invalid_request", "message": "id/method required"}},
            )

        if method == "agent":
            message_raw = str(params.get("message") or "")
            session_key = str(params.get("sessionKey") or "main").strip() or "main"
            agent_id = str(params.get("agentId") or "main").strip() or "main"
            idem = str(params.get("idempotencyKey") or "").strip()
            if not idem:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "idempotencyKey required"}}

            reset_triggered, message = _parse_reset_command(message_raw)

            # Resolve sessionId:
            # - If reset triggered: always mint a new sessionId (OpenClaw-style).
            # - Else: prefer explicit sessionId param if provided; otherwise reuse latest session for sessionKey.
            provided_session_id = str(params.get("sessionId") or "").strip()
            session_id = provided_session_id
            if reset_triggered or not session_id:
                # Fast-path: reuse in-memory latest session id when available.
                try:
                    k = (str(agent_id).strip().lower() or "main", str(session_key).strip())
                    mem_latest = state.latest_session_by_key.get(k)
                except Exception:
                    mem_latest = None
                if (not reset_triggered) and mem_latest:
                    session_id = str(mem_latest)
                else:
                    latest = None
                    try:
                        latest = session_manager.find_latest_by_session_key(session_key, agent_id=agent_id)  # type: ignore[attr-defined]
                    except Exception:
                        latest = None
                    if reset_triggered or not latest:
                        session_id = str(uuid.uuid4())
                    else:
                        session_id = str(latest.session_id)

            if not message and not reset_triggered:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "message required"}}

            cached = state.get_dedupe(f"agent:{idem}")
            if cached:
                return {"id": req_id, "ok": cached.ok, "payload": cached.payload, "error": cached.error}

            # Ensure the chosen session exists immediately. This avoids races where we return an
            # accepted response but the background run hasn't yet persisted the session entry.
            try:
                session_manager.get_or_create_session(  # type: ignore[attr-defined]
                    session_id=session_id,
                    session_key=session_key,
                    agent_id=agent_id,
                )
            except Exception:
                pass
            try:
                state.latest_session_by_key[(str(agent_id).strip().lower() or "main", str(session_key).strip())] = str(session_id)
            except Exception:
                pass

            # If this was a pure reset (no remaining user message), create the new session entry
            # synchronously so follow-up calls can reuse the latest session immediately.
            if reset_triggered and not message:
                final_payload = {
                    "runId": None,
                    "status": "ok",
                    "summary": "reset",
                    "sessionId": session_id,
                    "reset": True,
                }
                state.set_dedupe(
                    f"agent:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=final_payload)
                )
                return {"id": req_id, "ok": True, "payload": final_payload}

            run_id = str(params.get("runId") or state.new_run_id())
            state.ensure_run(run_id=run_id, session_key=session_key, agent_id=agent_id)

            accepted = {
                "runId": run_id,
                "status": "accepted",
                "acceptedAt": _now_ms(),
                "sessionId": session_id,
                "reset": reset_triggered,
            }
            state.set_dedupe(f"agent:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=accepted))

            async def _run() -> None:
                try:
                    # Resolve per-agent workspace dir (auto-creates agent if missing).
                    workspace_dir = agent_manager.get_or_create(agent_id).workspace_dir
                    bootstrap = load_bootstrap_system_prompt(workspace_dir)
                    extra = str(params.get("extraSystemPrompt") or "").strip()
                    extra_system_prompt = (
                        f"{bootstrap}\n\n{extra}".strip() if bootstrap else (extra or None)
                    )
                    result = await runner.run(
                        AgentRunParams(
                            message=message,
                            run_id=run_id,
                            session_key=session_key,
                            session_id=session_id,
                            agent_id=agent_id,
                            channel=str(params.get("channel") or "internal"),
                            deliver=bool(params.get("deliver") is True),
                            sandbox=bool(params.get("sandbox") is True),
                            extra_system_prompt=extra_system_prompt,
                            thinking_level=str(params.get("thinkingLevel") or "").strip() or None,
                            reasoning_level=str(params.get("reasoningLevel") or "").strip() or None,
                            workspace_dir=workspace_dir,
                        )
                    )
                    final_payload = {
                        "runId": run_id,
                        "status": "ok",
                        "summary": "completed",
                        "sessionId": session_id,
                        "result": {"meta": asdict(result.meta)},
                    }
                    state.set_dedupe(
                        f"agent:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=final_payload)
                    )
                except Exception as e:
                    err = {"code": "unavailable", "message": str(e)}
                    final_payload = {"runId": run_id, "status": "error", "summary": str(e), "sessionId": session_id}
                    state.set_dedupe(f"agent:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=False, payload=final_payload, error=err))

            asyncio.create_task(_run())
            return {"id": req_id, "ok": True, "payload": accepted, "runId": run_id}

        if method == "agent.wait":
            run_id = str(params.get("runId") or "").strip()
            timeout_ms = params.get("timeoutMs")
            if not run_id:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "runId required"}}
            timeout_ms_int = resolve_agent_wait_timeout_ms(timeout_ms)
            timeout_ms_int = max(0, timeout_ms_int)

            rec = state.runs.get(run_id)
            if rec and rec.snapshot:
                snap = rec.snapshot
                return {
                    "id": req_id,
                    "ok": True,
                    "payload": {
                        "runId": snap.run_id,
                        "status": snap.status,
                        "startedAt": snap.started_at,
                        "endedAt": snap.ended_at,
                        "error": snap.error,
                        "replyText": snap.reply_text,
                        "stopReason": snap.stop_reason,
                    },
                }

            if timeout_ms_int <= 0:
                return {"id": req_id, "ok": True, "payload": {"runId": run_id, "status": "timeout"}}

            rec = state.ensure_run(run_id=run_id, session_key=run_id)
            try:
                await asyncio.wait_for(rec.done.wait(), timeout=timeout_ms_int / 1000.0)
            except asyncio.TimeoutError:
                return {"id": req_id, "ok": True, "payload": {"runId": run_id, "status": "timeout"}}

            snap = rec.snapshot
            if not snap:
                return {"id": req_id, "ok": True, "payload": {"runId": run_id, "status": "timeout"}}
            return {
                "id": req_id,
                "ok": True,
                "payload": {
                    "runId": snap.run_id,
                    "status": snap.status,
                    "startedAt": snap.started_at,
                    "endedAt": snap.ended_at,
                    "error": snap.error,
                    "replyText": snap.reply_text,
                    "stopReason": snap.stop_reason,
                },
            }

        if method == "agent.session.history":
            agent_id = str(params.get("agentId") or "").strip() or "main"
            requested = str(params.get("sessionKey") or "desktop-app").strip() or "desktop-app"
            # Desktop UI uses sessionKey "desktop-app"; the agent RPC default when omitted is "main".
            # Try the requested key first, then common keys, and keep the session with newest updated_at.
            key_candidates: List[str] = []
            for k in (requested, "desktop-app", "main"):
                if k and k not in key_candidates:
                    key_candidates.append(k)

            def _openai_content_to_text(content: Any) -> str:
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts: List[str] = []
                    for item in content:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict):
                            t = item.get("text") or item.get("content")
                            if t is not None:
                                parts.append(str(t))
                    return "\n".join(parts)
                return ""

            latest = None
            for sk in key_candidates:
                try:
                    ent = session_manager.find_latest_by_session_key(sk, agent_id=agent_id)  # type: ignore[attr-defined]
                except TypeError:
                    ent = session_manager.find_latest_by_session_key(sk)
                if not ent:
                    continue
                if latest is None:
                    latest = ent
                    continue
                a = int(getattr(ent, "updated_at", 0) or 0)
                b = int(getattr(latest, "updated_at", 0) or 0)
                if a > b:
                    latest = ent

            if not latest:
                return {"id": req_id, "ok": True, "payload": {"sessionId": None, "messages": []}}

            sid = str(latest.session_id)
            try:
                transcript_file = session_manager.resolve_transcript_path(sid, agent_id=agent_id)  # type: ignore[attr-defined]
            except TypeError:
                transcript_file = session_manager.resolve_transcript_path(sid)

            try:
                from ..agents.session.transcript import build_messages_from_leaf

                raw_msgs = build_messages_from_leaf(transcript_file=transcript_file, limit=500)
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"agent.session.history failed: {e}"},
                }

            ui_messages: List[Dict[str, Any]] = []
            for m in raw_msgs:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role") or "").strip().lower()
                if role in ("tool",):
                    continue
                if role not in ("user", "assistant", "system"):
                    continue
                text = _openai_content_to_text(m.get("content")).strip()
                if not text:
                    continue
                ui_role = "user" if role == "user" else "assistant"
                ui_messages.append({"role": ui_role, "text": text})

            return {
                "id": req_id,
                "ok": True,
                "payload": {"sessionId": sid, "messages": ui_messages},
            }

        if method == "agents.list":
            try:
                from ..config.paths import normalize_agent_id, resolve_agent_sessions_file
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"agents.list failed: {e}"},
                }
            items: List[Dict[str, Any]] = []
            for raw_id in agent_manager.list_agents():
                aid = normalize_agent_id(raw_id)
                cfg_existing = agent_manager.get(aid)
                configured = cfg_existing is not None
                cfg = cfg_existing or agent_manager.get_or_create(aid)
                sessions_file = resolve_agent_sessions_file(aid)
                run_st = _run_status_for_agent(state, aid)
                llm_safe = None
                llm_key_configured = False
                if isinstance(cfg.llm, dict) and cfg.llm:
                    llm_safe = {x: y for x, y in cfg.llm.items() if x != "api_key"}
                    llm_key_configured = bool(
                        str(cfg.llm.get("api_key") or "").strip()
                    )
                skills_out: Optional[List[str]] = None
                if cfg.skills is not None:
                    skills_out = list(cfg.skills)
                items.append(
                    {
                        "agentId": aid,
                        "configured": configured,
                        "agentDir": cfg.agent_dir,
                        "workspaceDir": cfg.workspace_dir,
                        "sessionsFile": sessions_file,
                        "createdAt": cfg.created_at,
                        "updatedAt": cfg.updated_at,
                        "avatar": cfg.avatar,
                        "llm": llm_safe,
                        "llmApiKeyConfigured": llm_key_configured,
                        "skills": skills_out,
                        "runStatus": run_st,
                    }
                )
            return {"id": req_id, "ok": True, "payload": {"agents": items}}

        if method == "agents.update_skills":
            raw_id = str(params.get("agentId") or "").strip()
            if not raw_id:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "agentId is required"},
                }
            if "skills" not in params:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "skills is required (use null to clear)"},
                }
            skills_raw = params.get("skills")
            try:
                cfg = agent_manager.update_skills(raw_id, skills_raw)
            except ValueError as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": str(e)},
                }
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            out = None if cfg.skills is None else list(cfg.skills)
            return {
                "id": req_id,
                "ok": True,
                "payload": {"agentId": cfg.agent_id, "skills": out},
            }

        if method == "stats.agent.get":
            raw_aid = params.get("agentId", params.get("agent_id"))
            agent_id = str(raw_aid or "").strip()
            if not agent_id:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "agentId required"},
                }
            try:
                from ..agents.stats.agent_usage import get_agent_stats_path, load_agent_stats
                from ..config.paths import normalize_agent_id

                aid = normalize_agent_id(agent_id)
                stats = load_agent_stats(aid)
                path = str(get_agent_stats_path(aid))
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"stats.agent.get failed: {e}"},
                }
            return {
                "id": req_id,
                "ok": True,
                "payload": {"agentId": aid, "path": path, "stats": stats},
            }

        if method == "stats.agents.list":
            try:
                from ..agents.stats.agent_usage import get_agent_stats_path, load_agent_stats
                from ..config.paths import normalize_agent_id

                rows: List[Dict[str, Any]] = []
                for raw_id in agent_manager.list_agents():
                    aid = normalize_agent_id(raw_id)
                    st = load_agent_stats(aid)
                    rows.append(
                        {
                            "agentId": aid,
                            "path": str(get_agent_stats_path(aid)),
                            "llmUsage": st.get("llmUsage"),
                            "updatedAtMs": st.get("updatedAtMs"),
                        }
                    )
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"stats.agents.list failed: {e}"},
                }
            return {"id": req_id, "ok": True, "payload": {"agents": rows}}

        if method == "agents.resolve_defaults":
            try:
                from ..config.paths import (
                    normalize_agent_id,
                    resolve_agent_dir,
                )
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            hint = str(params.get("agentId") or "").strip() or "new-agent"
            aid = normalize_agent_id(hint)
            return {
                "id": req_id,
                "ok": True,
                "payload": {
                    "agentId": aid,
                    "agentDir": resolve_agent_dir(aid),
                    # UX: prefer a human-friendly "~" default shown in the UI.
                    # The create RPC expands "~" to an absolute path.
                    "workspaceDir": f"~/.mw4agent/agents/{aid}",
                },
            }

        if method == "agents.create":
            raw_id = str(params.get("agentId") or "").strip()
            if not raw_id:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "agentId is required"},
                }
            workspace_opt = str(params.get("workspaceDir") or "").strip() or None
            llm_raw = params.get("llm")
            llm = llm_raw if isinstance(llm_raw, dict) else None
            avatar_opt = str(params.get("avatar") or "").strip() or None
            try:
                cfg = agent_manager.create_agent(
                    raw_id,
                    workspace_dir=workspace_opt,
                    llm=llm,
                    avatar=avatar_opt,
                )
            except ValueError as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": str(e)},
                }
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            return {
                "id": req_id,
                "ok": True,
                "payload": {
                    "agentId": cfg.agent_id,
                    "agentDir": cfg.agent_dir,
                    "workspaceDir": cfg.workspace_dir,
                    "llm": cfg.llm,
                    "avatar": cfg.avatar,
                },
            }

        if method == "agents.set_avatar":
            raw_id = str(params.get("agentId") or "").strip()
            if not raw_id:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "agentId is required"},
                }
            av_raw = params.get("avatar")
            avatar_param = "" if av_raw is None else str(av_raw)
            try:
                cfg = agent_manager.set_avatar(raw_id, avatar=avatar_param)
            except ValueError as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": str(e)},
                }
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            return {
                "id": req_id,
                "ok": True,
                "payload": {"agentId": cfg.agent_id, "avatar": cfg.avatar},
            }

        if method == "agents.update_llm":
            raw_id = str(params.get("agentId") or "").strip()
            if not raw_id:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "agentId is required"},
                }
            llm_raw = params.get("llm")
            llm = llm_raw if isinstance(llm_raw, dict) else None
            try:
                cfg = agent_manager.update_llm(raw_id, llm)
            except ValueError as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": str(e)},
                }
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            llm_resp = dict(cfg.llm) if cfg.llm else {}
            llm_resp.pop("api_key", None)
            return {
                "id": req_id,
                "ok": True,
                "payload": {
                    "agentId": cfg.agent_id,
                    "llm": llm_resp or None,
                    "llmApiKeyConfigured": bool(
                        cfg.llm and str(cfg.llm.get("api_key") or "").strip()
                    ),
                },
            }

        if method == "agents.delete":
            raw_id = str(params.get("agentId") or "").strip()
            if not raw_id:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "agentId is required"},
                }
            allow_main = bool(params.get("allowMain") is True)
            try:
                deleted = agent_manager.delete(raw_id, allow_main=allow_main)
            except ValueError as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": str(e)},
                }
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            return {"id": req_id, "ok": True, "payload": {"deleted": bool(deleted)}}

        if method == "agents.workspace_file.read":
            agent_id = str(params.get("agentId") or "").strip()
            rel_path = str(params.get("path") or "").strip()
            if not agent_id:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "agentId is required"}}
            if not rel_path:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "path is required"}}
            try:
                resolved_rel, abs_path = _resolve_agent_workspace_file_abs(
                    agent_manager=agent_manager,
                    agent_id=agent_id,
                    rel_path=rel_path,
                    prefer_existing_case_variant=True,
                )
                if not os.path.isfile(abs_path):
                    return {"id": req_id, "ok": True, "payload": {"path": resolved_rel, "text": "", "missing": True}}
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                return {"id": req_id, "ok": True, "payload": {"path": resolved_rel, "text": text, "missing": False}}
            except ValueError as e:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": str(e)}}
            except Exception as e:
                return {"id": req_id, "ok": False, "error": {"code": "unavailable", "message": str(e)}}

        if method == "agents.workspace_file.write":
            agent_id = str(params.get("agentId") or "").strip()
            rel_path = str(params.get("path") or "").strip()
            text = params.get("text")
            if not agent_id:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "agentId is required"}}
            if not rel_path:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "path is required"}}
            if text is None:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "text is required"}}
            try:
                # Prefer writing back to the already-existing case variant to avoid creating a
                # second file (e.g. MEMORY.md vs memory.md) and unintentionally "overwriting" by divergence.
                resolved_rel, abs_path = _resolve_agent_workspace_file_abs(
                    agent_manager=agent_manager,
                    agent_id=agent_id,
                    rel_path=rel_path,
                    prefer_existing_case_variant=True,
                )
                payload = str(text)
                if len(payload) > 300_000:
                    raise ValueError("text too large")
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(payload)
                return {"id": req_id, "ok": True, "payload": {"path": resolved_rel, "saved": True}}
            except ValueError as e:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": str(e)}}
            except Exception as e:
                return {"id": req_id, "ok": False, "error": {"code": "unavailable", "message": str(e)}}

        if method == "orchestrate.run":
            message = str(params.get("message") or "").strip()
            session_key = str(params.get("sessionKey") or "orchestrator").strip() or "orchestrator"
            participants_raw = params.get("participants")
            participants = (
                [str(x) for x in participants_raw if str(x).strip()]
                if isinstance(participants_raw, list)
                else []
            )
            max_rounds = params.get("maxRounds")
            name = str(params.get("name") or "").strip()
            strategy = str(params.get("strategy") or "round_robin").strip() or "round_robin"
            router_llm_raw = params.get("routerLlm")
            router_llm = dict(router_llm_raw) if isinstance(router_llm_raw, dict) else None
            rar_raw = params.get("routerAgentRoles") or params.get("router_agent_roles")
            router_agent_roles = dict(rar_raw) if isinstance(rar_raw, dict) else None
            try:
                max_rounds_int = int(max_rounds) if isinstance(max_rounds, (int, float, str)) and str(max_rounds).strip() else 8
            except Exception:
                max_rounds_int = 8
            idem = str(params.get("idempotencyKey") or "").strip()
            if not idem:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "idempotencyKey required"}}
            if not message:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "message required"}}
            cached = state.get_dedupe(f"orch:{idem}")
            if cached:
                return {"id": req_id, "ok": cached.ok, "payload": cached.payload, "error": cached.error}
            dag_raw = params.get("dag")
            dag = dict(dag_raw) if isinstance(dag_raw, dict) else None
            sup_pl_raw = params.get("supervisorPipeline") or params.get("supervisor_pipeline")
            supervisor_pipeline = (
                [str(x) for x in sup_pl_raw if str(x).strip()]
                if isinstance(sup_pl_raw, list)
                else None
            )
            sup_llm_raw = params.get("supervisorLlm") or params.get("supervisor_llm")
            supervisor_llm_dict = dict(sup_llm_raw) if isinstance(sup_llm_raw, dict) else None
            sup_max_raw = params.get("supervisorMaxIterations") or params.get("supervisor_max_iterations")
            try:
                sup_run_max = int(sup_max_raw) if sup_max_raw is not None else None
            except (TypeError, ValueError):
                sup_run_max = None
            sup_llm_retries_raw = params.get("supervisorLlmMaxRetries") or params.get(
                "supervisor_llm_max_retries"
            )
            try:
                sup_llm_retries_int = (
                    int(sup_llm_retries_raw) if sup_llm_retries_raw is not None else None
                )
            except (TypeError, ValueError):
                sup_llm_retries_int = None
            _orl_run = params.get("orchReplyLanguage") or params.get("orch_reply_language")
            orch_reply_language_run = (
                str(_orl_run).strip() if _orl_run is not None and str(_orl_run).strip() else None
            )
            try:
                st = orchestrator.create(
                    session_key=session_key,
                    name=name,
                    participants=participants,
                    max_rounds=max_rounds_int,
                    strategy=strategy,
                    router_llm=router_llm,
                    router_agent_roles=router_agent_roles,
                    dag=dag,
                    supervisor_pipeline=supervisor_pipeline,
                    supervisor_llm=supervisor_llm_dict,
                    supervisor_max_iterations=sup_run_max,
                    supervisor_llm_max_retries=sup_llm_retries_int,
                    orch_reply_language=orch_reply_language_run,
                )
                orchestrator.send(orch_id=st.orchId, message=message)
            except ValueError as e:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": str(e)}}
            payload = {
                "orchId": st.orchId,
                "status": "running",
                "sessionKey": st.sessionKey,
            }
            state.set_dedupe(f"orch:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=payload))
            return {"id": req_id, "ok": True, "payload": payload}

        if method == "orchestrate.create":
            session_key = str(params.get("sessionKey") or "orchestrator").strip() or "orchestrator"
            name = str(params.get("name") or "").strip()
            participants_raw = params.get("participants")
            participants = (
                [str(x) for x in participants_raw if str(x).strip()]
                if isinstance(participants_raw, list)
                else []
            )
            max_rounds = params.get("maxRounds")
            strategy = str(params.get("strategy") or "round_robin").strip() or "round_robin"
            router_llm_raw = params.get("routerLlm")
            router_llm = dict(router_llm_raw) if isinstance(router_llm_raw, dict) else None
            rar_raw = params.get("routerAgentRoles") or params.get("router_agent_roles")
            router_agent_roles = dict(rar_raw) if isinstance(rar_raw, dict) else None
            try:
                max_rounds_int = int(max_rounds) if isinstance(max_rounds, (int, float, str)) and str(max_rounds).strip() else 8
            except Exception:
                max_rounds_int = 8
            idem = str(params.get("idempotencyKey") or "").strip()
            if not idem:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "idempotencyKey required"}}
            cached = state.get_dedupe(f"orch:create:{idem}")
            if cached:
                return {"id": req_id, "ok": cached.ok, "payload": cached.payload, "error": cached.error}
            dag_raw = params.get("dag")
            dag = dict(dag_raw) if isinstance(dag_raw, dict) else None
            sup_pl_raw = params.get("supervisorPipeline") or params.get("supervisor_pipeline")
            supervisor_pipeline = (
                [str(x) for x in sup_pl_raw if str(x).strip()]
                if isinstance(sup_pl_raw, list)
                else None
            )
            sup_llm_raw = params.get("supervisorLlm") or params.get("supervisor_llm")
            supervisor_llm_dict = dict(sup_llm_raw) if isinstance(sup_llm_raw, dict) else None
            sup_max_raw = params.get("supervisorMaxIterations") or params.get("supervisor_max_iterations")
            try:
                sup_max_int = int(sup_max_raw) if sup_max_raw is not None else None
            except (TypeError, ValueError):
                sup_max_int = None
            sup_llm_retries_raw = params.get("supervisorLlmMaxRetries") or params.get(
                "supervisor_llm_max_retries"
            )
            try:
                sup_llm_retries_int = (
                    int(sup_llm_retries_raw) if sup_llm_retries_raw is not None else None
                )
            except (TypeError, ValueError):
                sup_llm_retries_int = None
            _orl_create = params.get("orchReplyLanguage") or params.get("orch_reply_language")
            orch_reply_language_create = (
                str(_orl_create).strip()
                if _orl_create is not None and str(_orl_create).strip()
                else None
            )
            try:
                st = orchestrator.create(
                    session_key=session_key,
                    name=name,
                    participants=participants,
                    max_rounds=max_rounds_int,
                    strategy=strategy,
                    router_llm=router_llm,
                    router_agent_roles=router_agent_roles,
                    dag=dag,
                    supervisor_pipeline=supervisor_pipeline,
                    supervisor_llm=supervisor_llm_dict,
                    supervisor_max_iterations=sup_max_int,
                    supervisor_llm_max_retries=sup_llm_retries_int,
                    orch_reply_language=orch_reply_language_create,
                )
            except ValueError as e:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": str(e)}}
            payload = {"orchId": st.orchId, "status": st.status, "sessionKey": st.sessionKey}
            state.set_dedupe(f"orch:create:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=payload))
            return {"id": req_id, "ok": True, "payload": payload}

        if method == "orchestrate.update":
            orch_id = str(params.get("orchId") or "").strip()
            if not orch_id:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "orchId is required"},
                }
            session_key = str(params.get("sessionKey") or "orchestrator").strip() or "orchestrator"
            name = str(params.get("name") or "").strip()
            participants_raw = params.get("participants")
            participants = (
                [str(x) for x in participants_raw if str(x).strip()]
                if isinstance(participants_raw, list)
                else []
            )
            max_rounds = params.get("maxRounds")
            strategy = str(params.get("strategy") or "round_robin").strip() or "round_robin"
            router_llm_raw = params.get("routerLlm")
            router_llm = dict(router_llm_raw) if isinstance(router_llm_raw, dict) else None
            rar_raw = params.get("routerAgentRoles") or params.get("router_agent_roles")
            router_agent_roles = dict(rar_raw) if isinstance(rar_raw, dict) else None
            try:
                max_rounds_int = int(max_rounds) if isinstance(max_rounds, (int, float, str)) and str(max_rounds).strip() else 8
            except Exception:
                max_rounds_int = 8
            idem = str(params.get("idempotencyKey") or "").strip()
            if not idem:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "idempotencyKey required"}}
            cached = state.get_dedupe(f"orch:update:{idem}")
            if cached:
                return {"id": req_id, "ok": cached.ok, "payload": cached.payload, "error": cached.error}
            dag_raw = params.get("dag")
            dag = dict(dag_raw) if isinstance(dag_raw, dict) else None
            sup_pl_raw = params.get("supervisorPipeline") or params.get("supervisor_pipeline")
            supervisor_pipeline = (
                [str(x) for x in sup_pl_raw if str(x).strip()]
                if isinstance(sup_pl_raw, list)
                else None
            )
            sup_llm_raw = params.get("supervisorLlm") or params.get("supervisor_llm")
            supervisor_llm_dict = dict(sup_llm_raw) if isinstance(sup_llm_raw, dict) else None
            sup_max_raw = params.get("supervisorMaxIterations") or params.get("supervisor_max_iterations")
            try:
                sup_max_int = int(sup_max_raw) if sup_max_raw is not None else None
            except (TypeError, ValueError):
                sup_max_int = None
            sup_llm_retries_raw = params.get("supervisorLlmMaxRetries") or params.get(
                "supervisor_llm_max_retries"
            )
            try:
                sup_llm_retries_int = (
                    int(sup_llm_retries_raw) if sup_llm_retries_raw is not None else None
                )
            except (TypeError, ValueError):
                sup_llm_retries_int = None
            _orl_upd = params.get("orchReplyLanguage") or params.get("orch_reply_language")
            orch_reply_language_update = (
                str(_orl_upd).strip() if _orl_upd is not None and str(_orl_upd).strip() else None
            )
            try:
                st = orchestrator.update(
                    orch_id,
                    session_key=session_key,
                    name=name,
                    participants=participants,
                    max_rounds=max_rounds_int,
                    strategy=strategy,
                    router_llm=router_llm,
                    router_agent_roles=router_agent_roles,
                    dag=dag,
                    supervisor_pipeline=supervisor_pipeline,
                    supervisor_llm=supervisor_llm_dict,
                    supervisor_max_iterations=sup_max_int,
                    supervisor_llm_max_retries=sup_llm_retries_int,
                    orch_reply_language=orch_reply_language_update,
                )
            except ValueError as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": str(e)},
                }
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            payload = {"orchId": st.orchId, "status": st.status, "sessionKey": st.sessionKey}
            state.set_dedupe(f"orch:update:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=payload))
            return {"id": req_id, "ok": True, "payload": payload}

        if method == "orchestrate.list":
            items = []
            for st in orchestrator.list():
                items.append(
                    {
                        "orchId": st.orchId,
                        "name": st.name,
                        "status": st.status,
                        "sessionKey": st.sessionKey,
                        "strategy": st.strategy,
                        "maxRounds": st.maxRounds,
                        "participants": st.participants,
                        "currentRound": st.currentRound,
                        "createdAt": st.createdAt,
                        "updatedAt": st.updatedAt,
                        "error": st.error,
                        "orchReplyLanguage": getattr(st, "orchReplyLanguage", "auto"),
                    }
                )
            return {"id": req_id, "ok": True, "payload": {"orchestrations": items}}

        if method == "orchestrate.delete":
            orch_id = str(params.get("orchId") or "").strip()
            if not orch_id:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "orchId is required"}}
            try:
                deleted = orchestrator.delete(orch_id)
            except ValueError as e:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": str(e)}}
            except Exception as e:
                return {"id": req_id, "ok": False, "error": {"code": "unavailable", "message": str(e)}}
            return {"id": req_id, "ok": True, "payload": {"deleted": bool(deleted)}}

        if method == "orchestrate.get":
            orch_id = str(params.get("orchId") or "").strip()
            if not orch_id:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "orchId is required"}}
            st = orchestrator.get(orch_id)
            if not st:
                return {"id": req_id, "ok": False, "error": {"code": "not_found", "message": "orchestration not found"}}
            rl = getattr(st, "routerLlm", None)
            router_public = None
            router_key_configured = False
            if isinstance(rl, dict) and rl:
                router_public = {
                    k: v for k, v in rl.items() if k not in ("api_key", "apiKey")
                }
                router_key_configured = bool(
                    str(rl.get("api_key") or rl.get("apiKey") or "").strip()
                )
            sl = getattr(st, "supervisorLlm", None)
            supervisor_public = None
            supervisor_key_configured = False
            if isinstance(sl, dict) and sl:
                supervisor_public = {
                    k: v for k, v in sl.items() if k not in ("api_key", "apiKey")
                }
                supervisor_key_configured = bool(
                    str(sl.get("api_key") or sl.get("apiKey") or "").strip()
                )
            payload = {
                "orchId": st.orchId,
                "name": st.name,
                "sessionKey": st.sessionKey,
                "status": st.status,
                "strategy": st.strategy,
                "maxRounds": st.maxRounds,
                "currentRound": st.currentRound,
                "participants": st.participants,
                "messages": [asdict(m) for m in st.messages],
                "error": st.error,
                "createdAt": st.createdAt,
                "updatedAt": st.updatedAt,
                "orchSchemaVersion": getattr(st, "orchSchemaVersion", 1),
                "dagSpec": getattr(st, "dagSpec", None),
                "dagProgress": getattr(st, "dagProgress", None),
                "dagParallelism": getattr(st, "dagParallelism", 4),
                "routerLlm": router_public,
                "routerApiKeyConfigured": router_key_configured,
                "routerAgentRoles": dict(getattr(st, "routerAgentRoles", None) or {}),
                "supervisorPipeline": list(getattr(st, "supervisorPipeline", None) or []),
                "supervisorMaxIterations": int(getattr(st, "supervisorMaxIterations", 5) or 5),
                "supervisorLlmMaxRetries": int(getattr(st, "supervisorLlmMaxRetries", 12) or 12),
                "supervisorIteration": int(getattr(st, "supervisorIteration", 0) or 0),
                "supervisorLastDecision": getattr(st, "supervisorLastDecision", None),
                "supervisorLlm": supervisor_public,
                "supervisorApiKeyConfigured": supervisor_key_configured,
                "orchReplyLanguage": getattr(st, "orchReplyLanguage", "auto"),
            }
            return {"id": req_id, "ok": True, "payload": payload}

        if method == "orchestrate.send":
            orch_id = str(params.get("orchId") or "").strip()
            message = str(params.get("message") or "").strip()
            idem = str(params.get("idempotencyKey") or "").strip()
            rl_raw = params.get("reasoningLevel") if "reasoningLevel" in params else params.get("reasoning_level")
            reasoning_level = None
            if rl_raw is not None and str(rl_raw).strip():
                x = str(rl_raw).strip().lower()
                if x in ("off", "on", "stream"):
                    reasoning_level = x
            if not orch_id:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "orchId is required"}}
            if not idem:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "idempotencyKey required"}}
            cached = state.get_dedupe(f"orch:send:{orch_id}:{idem}")
            if cached:
                return {"id": req_id, "ok": cached.ok, "payload": cached.payload, "error": cached.error}
            target_raw = params.get("targetAgent") if "targetAgent" in params else params.get("target_agent")
            target_agent = (
                str(target_raw).strip()
                if target_raw is not None and str(target_raw).strip()
                else None
            )
            try:
                st = orchestrator.send(
                    orch_id=orch_id,
                    message=message,
                    reasoning_level=reasoning_level,
                    target_agent=target_agent,
                )
            except ValueError as e:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": str(e)}}
            payload = {"orchId": st.orchId, "status": st.status, "currentRound": st.currentRound}
            state.set_dedupe(
                f"orch:send:{orch_id}:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=payload)
            )
            return {"id": req_id, "ok": True, "payload": payload}

        if method == "orchestrate.wait":
            orch_id = str(params.get("orchId") or "").strip()
            if not orch_id:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "orchId is required"}}
            timeout_ms = params.get("timeoutMs")
            try:
                timeout_ms_int = int(timeout_ms) if isinstance(timeout_ms, (int, float, str)) and str(timeout_ms).strip() else 15_000
            except Exception:
                timeout_ms_int = 15_000
            timeout_ms_int = max(0, min(120_000, timeout_ms_int))
            deadline = _now_ms() + timeout_ms_int
            while _now_ms() < deadline:
                st = orchestrator.get(orch_id)
                if not st:
                    return {"id": req_id, "ok": False, "error": {"code": "not_found", "message": "orchestration not found"}}
                if st.status not in {"accepted", "running"}:
                    break
                await asyncio.sleep(0.4)
            st = orchestrator.get(orch_id)
            if not st:
                return {"id": req_id, "ok": False, "error": {"code": "not_found", "message": "orchestration not found"}}
            return {
                "id": req_id,
                "ok": True,
                "payload": {
                    "orchId": st.orchId,
                    "status": st.status,
                    "currentRound": st.currentRound,
                },
            }

        if method == "llm.providers.list":
            try:
                from ..llm.backends import list_providers, list_provider_infos
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            provs = ["echo", *list(list_providers())]
            infos = [{"id": "echo", "default_base_url": None, "default_model": "gpt-4o-mini"}]
            try:
                infos.extend(list_provider_infos())
            except Exception:
                pass
            return {"id": req_id, "ok": True, "payload": {"providers": provs, "providerInfos": infos}}

        if method == "llm.test":
            try:
                from ..config.paths import normalize_agent_id
                from ..config.redact import is_redacted_placeholder
                from ..config.root import read_root_section
                from ..llm.backends import test_llm_connection
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            llm_in = params.get("llm")
            llm: Dict[str, Any] = dict(llm_in) if isinstance(llm_in, dict) else {}
            aid = str(params.get("agentId") or "").strip()
            if aid:
                ak = str(llm.get("api_key") or llm.get("apiKey") or "").strip()
                if not ak or is_redacted_placeholder(ak):
                    cfg_existing = agent_manager.get(normalize_agent_id(aid))
                    if cfg_existing and cfg_existing.llm:
                        sk = str(cfg_existing.llm.get("api_key") or "").strip()
                        if sk and not is_redacted_placeholder(sk):
                            llm = {**llm, "api_key": sk}
            ak2 = str(llm.get("api_key") or llm.get("apiKey") or "").strip()
            if not ak2 or is_redacted_placeholder(ak2):
                sec_llm = read_root_section("llm")
                rk = str(sec_llm.get("api_key") or "").strip()
                if rk and not is_redacted_placeholder(rk):
                    llm = {**llm, "api_key": rk}
            try:
                result = test_llm_connection(llm)
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": str(e)},
                }
            return {"id": req_id, "ok": True, "payload": result}

        if method == "skills.list":
            try:
                from ..agents.skills.snapshot import build_skill_snapshot
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"skills.list failed: {e}"},
                }
            workspace_dir = str(params.get("workspaceDir") or "").strip() or None
            try:
                snapshot = build_skill_snapshot(workspace_dir=workspace_dir)
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"skills.snapshot failed: {e}"},
                }
            payload: Dict[str, Any] = {
                "skills": snapshot.get("skills") or [],
                "count": int(snapshot.get("count") or 0),
                "version": snapshot.get("version"),
                "sources": snapshot.get("sources") or [],
                "promptTruncated": bool(snapshot.get("prompt_truncated")),
                "promptCompact": bool(snapshot.get("prompt_compact")),
                "promptCount": snapshot.get("prompt_count"),
                "skillFilter": snapshot.get("skill_filter") or [],
                "filteredOut": snapshot.get("filtered_out") or [],
            }
            if params.get("includePrompt") is True:
                payload["prompt"] = snapshot.get("prompt") or ""
            return {"id": req_id, "ok": True, "payload": payload}

        if method == "health":
            return {"id": req_id, "ok": True, "payload": await health()}

        if method == "tools.config":
            # Return current tools policy config for dashboard inspection.
            try:
                from ..config import get_default_config_manager
                from ..agents.tools.policy import (
                    resolve_sandbox_tool_policy_config,
                    resolve_tool_policy_config,
                )
                from ..agents.tools.sandbox_workspace import resolve_sandbox_sessions_root

                cfg_mgr = get_default_config_manager()
                base_policy = resolve_tool_policy_config(cfg_mgr)
                sandbox_policy = resolve_sandbox_tool_policy_config(cfg_mgr)
                try:
                    sandbox_root = resolve_sandbox_sessions_root(cfg_mgr)
                except Exception:
                    sandbox_root = None
                raw_tools = cfg_mgr.read_config("tools", default={})
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"failed to read tools config: {e}"},
                }
            payload = {
                "basePolicy": {
                    "profile": base_policy.profile,
                    "allow": base_policy.allow,
                    "deny": base_policy.deny,
                },
                "sandboxPolicy": {
                    "enabled": sandbox_policy.enabled,
                    "allow": sandbox_policy.allow,
                    "deny": sandbox_policy.deny,
                    "directoryIsolation": sandbox_policy.directory_isolation,
                    "executionIsolation": sandbox_policy.execution_isolation,
                    "resolvedWorkspaceRoot": sandbox_root,
                },
                "raw": raw_tools,
            }
            return {"id": req_id, "ok": True, "payload": payload}

        if method == "config.get":
            # Return full root config (~/.mw4agent/mw4agent.json) for dashboard inspection.
            try:
                from ..config.redact import redact_secrets
                from ..config.root import get_root_config_path, read_root_config

                cfg = read_root_config()
                path = str(get_root_config_path())
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"failed to read root config: {e}"},
                }
            return {
                "id": req_id,
                "ok": True,
                "payload": {
                    "path": path,
                    "config": redact_secrets(cfg),
                },
            }

        if method == "config.sections.list":
            # List top-level config sections for dashboard editing.
            try:
                from ..config.root import read_root_config

                cfg = read_root_config()
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"failed to read root config: {e}"},
                }
            sections = sorted([k for k, v in (cfg or {}).items() if isinstance(k, str)])
            return {"id": req_id, "ok": True, "payload": {"sections": sections}}

        if method == "config.section.get":
            section = str(params.get("section") or "").strip()
            if not section:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "section required"}}
            try:
                from ..config.redact import redact_secrets
                from ..config.root import read_root_config

                cfg = read_root_config()
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"failed to read root config: {e}"},
                }
            raw = cfg.get(section)
            if isinstance(raw, dict):
                safe = redact_secrets(raw)
            elif isinstance(raw, list):
                safe = redact_secrets(raw)
            else:
                safe = raw
            return {"id": req_id, "ok": True, "payload": {"section": section, "value": safe}}

        if method == "config.section.set":
            section = str(params.get("section") or "").strip()
            value = params.get("value")
            if not section:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "section required"}}
            if section in ("__proto__", "prototype", "constructor"):
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "unsafe section name"}}
            if not isinstance(value, dict):
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "value must be an object"}}
            try:
                from ..config.redact import merge_preserve_redacted_secrets
                from ..config.root import read_root_section, write_root_section

                old_sec = read_root_section(section)
                merged = merge_preserve_redacted_secrets(old_sec, value)
                write_root_section(section, merged)
            except Exception as e:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "unavailable", "message": f"failed to write section: {e}"},
                }
            return {"id": req_id, "ok": True, "payload": {"section": section, "ok": True}}

        if method == "ls":
            raw_path = params.get("path")
            path = str(raw_path) if isinstance(raw_path, (str, bytes)) else "."
            if isinstance(raw_path, bytes):
                path = raw_path.decode("utf-8", errors="replace")
            path = path.strip() or "."
            if not _is_safe_rel_path(path):
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "unsafe path (relative paths only; '..' disallowed)"},
                }
            try:
                entries = sorted(os.listdir(path))
            except Exception as e:
                return {"id": req_id, "ok": False, "error": {"code": "not_found", "message": str(e)}}
            return {"id": req_id, "ok": True, "payload": {"path": path, "entries": entries}}

        if method == "node.list":
            nodes = state.node_registry.list_connected()
            return {"id": req_id, "ok": True, "payload": {"ts": _now_ms(), "nodes": nodes}}

        if method == "node.invoke":
            node_id = str(params.get("nodeId") or "").strip()
            command = str(params.get("command") or "").strip()
            if not node_id or not command:
                return {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_request", "message": "nodeId and command required"},
                }
            timeout_ms = 30_000
            if isinstance(params.get("timeoutMs"), (int, float)) and params["timeoutMs"] > 0:
                timeout_ms = int(params["timeoutMs"])
            idem = str(params.get("idempotencyKey") or "")
            invoke_params = params.get("params")
            if not isinstance(invoke_params, dict):
                invoke_params = {}
            result = await state.node_registry.invoke(
                node_id=node_id,
                command=command,
                params=invoke_params,
                timeout_ms=timeout_ms,
                idempotency_key=idem or None,
            )
            if not result.get("ok"):
                return {"id": req_id, "ok": False, "error": result.get("error", {"code": "unavailable", "message": "node invoke failed"})}
            return {"id": req_id, "ok": True, "payload": result.get("payload"), "payloadJSON": result.get("payloadJSON")}

        return {"id": req_id, "ok": False, "error": {"code": "method_not_found", "message": f"Unknown method: {method}"}}

    # --- Minimal dashboard SPA (served as static files) ---
    # The SPA lives in mw4agent/dashboard/static and talks to:
    # - POST /rpc for Gateway RPC
    # - WS  /ws  for agent event streams
    dashboard_static_dir = (
        Path(__file__).resolve().parent.parent / "dashboard" / "static"
    )
    if dashboard_static_dir.is_dir():
        # Serve the dashboard under /dashboard to avoid intercepting /ws and /rpc.
        app.mount(
            "/dashboard",
            StaticFiles(directory=str(dashboard_static_dir), html=True),
            name="dashboard",
        )

        @app.get("/")
        async def root_redirect() -> RedirectResponse:
            """Redirect bare `/` to the dashboard entry."""
            return RedirectResponse(url="/dashboard/")

    return app

