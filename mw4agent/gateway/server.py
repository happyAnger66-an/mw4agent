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
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..agents.runner.runner import AgentRunner
from ..agents.session.manager import SessionManager
from ..agents.types import AgentRunParams
from ..config.paths import ensure_workspace_dir, get_default_workspace_dir
from ..log import get_logger
from ..memory.bootstrap import load_bootstrap_system_prompt
from ..plugin import load_plugins
from .state import DedupeEntry, GatewayState, RunSnapshot
from .types import AgentEvent

logger = get_logger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)

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


def create_app(
    *,
    session_file: str = "mw4agent.sessions.json",
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
    session_manager = SessionManager(session_file)
    runner = AgentRunner(session_manager)

    # --- Feishu channel: if configured, webhook 挂载到 app 或 websocket 随进程启动（由配置 connection_mode 决定）---
    feishu_webhook_router = None
    feishu_ws_start: Optional[tuple] = None  # (plugin, dispatcher) for lifespan
    try:
        from ..config import read_root_section
        channels = read_root_section("channels", default={})
        feishu_cfg = channels.get("feishu") or {}
        app_id = (feishu_cfg.get("app_id") or "").strip() or os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = (feishu_cfg.get("app_secret") or "").strip() or os.getenv("FEISHU_APP_SECRET", "").strip()
        connection_mode = (feishu_cfg.get("connection_mode") or "webhook").strip().lower()
        if connection_mode not in ("webhook", "websocket"):
            connection_mode = "webhook"
        if app_id and app_secret:
            from ..channels.dispatcher import ChannelDispatcher, ChannelRuntime
            from ..channels.plugins.feishu import FeishuChannel
            from ..channels.registry import ChannelRegistry
            registry = ChannelRegistry()
            feishu_plugin = FeishuChannel(connection_mode=connection_mode, path="/feishu/webhook")
            registry.register_plugin(feishu_plugin)
            runtime = ChannelRuntime(
                session_manager=session_manager,
                agent_runner=runner,
                gateway_base_url=None,
            )
            dispatcher = ChannelDispatcher(runtime=runtime, registry=registry)
            if connection_mode == "webhook":
                feishu_webhook_router = feishu_plugin.get_webhook_router(on_inbound=dispatcher.dispatch_inbound)
                logger.info("Feishu channel enabled (webhook, will mount at /feishu/webhook)")
            else:
                feishu_ws_start = (feishu_plugin, dispatcher)
                logger.info("Feishu channel enabled (websocket, will start in lifespan)")
    except Exception as e:
        logger.debug("Feishu channel not started: %s", e)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if feishu_ws_start:
            plugin, disp = feishu_ws_start
            asyncio.create_task(plugin._run_ws_monitor(on_inbound=disp.dispatch_inbound))
        yield

    app = FastAPI(title="MW4Agent Gateway", version="0.1", lifespan=lifespan)
    if feishu_webhook_router is not None:
        app.include_router(feishu_webhook_router)

    # --- Bridge AgentRunner events -> Gateway WS broadcasts + run snapshots ---
    async def handle_agent_stream_event(evt) -> None:
        # evt can be mw4agent.agents.events.stream.StreamEvent (our internal)
        run_id = str(evt.data.get("run_id") or "")
        if not run_id:
            return

        rec = state.ensure_run(run_id=run_id, session_key=str(evt.data.get("session_key") or ""))
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
                state.mark_run_terminal(
                    run_id,
                    RunSnapshot(
                        run_id=run_id,
                        status="ok",
                        started_at=rec.started_at_ms,
                        ended_at=int(ended_at),
                        reply_text=rec.reply_text_buffer.strip() if rec.reply_text_buffer else None,
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

    runner.event_stream.subscribe("lifecycle", handle_agent_stream_event)
    runner.event_stream.subscribe("assistant", handle_agent_stream_event)
    runner.event_stream.subscribe("tool", handle_agent_stream_event)

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
            message = str(params.get("message") or "").strip()
            session_key = str(params.get("sessionKey") or "main").strip() or "main"
            session_id = str(params.get("sessionId") or "main").strip() or "main"
            agent_id = str(params.get("agentId") or "main").strip() or "main"
            idem = str(params.get("idempotencyKey") or "").strip()
            if not message or not idem:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "message and idempotencyKey required"}}

            cached = state.get_dedupe(f"agent:{idem}")
            if cached:
                return {"id": req_id, "ok": cached.ok, "payload": cached.payload, "error": cached.error}

            run_id = str(params.get("runId") or state.new_run_id())
            state.ensure_run(run_id=run_id, session_key=session_key)

            accepted = {"runId": run_id, "status": "accepted", "acceptedAt": _now_ms()}
            state.set_dedupe(f"agent:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=accepted))

            async def _run() -> None:
                try:
                    workspace_dir = ensure_workspace_dir()
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
                            extra_system_prompt=extra_system_prompt,
                            thinking_level=str(params.get("thinkingLevel") or "").strip() or None,
                            reasoning_level=str(params.get("reasoningLevel") or "").strip() or None,
                            workspace_dir=workspace_dir,
                        )
                    )
                    final_payload = {"runId": run_id, "status": "ok", "summary": "completed", "result": {"meta": asdict(result.meta)}}
                    state.set_dedupe(f"agent:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=True, payload=final_payload))
                except Exception as e:
                    err = {"code": "unavailable", "message": str(e)}
                    final_payload = {"runId": run_id, "status": "error", "summary": str(e)}
                    state.set_dedupe(f"agent:{idem}", DedupeEntry(ts_ms=_now_ms(), ok=False, payload=final_payload, error=err))

            asyncio.create_task(_run())
            return {"id": req_id, "ok": True, "payload": accepted, "runId": run_id}

        if method == "agent.wait":
            run_id = str(params.get("runId") or "").strip()
            timeout_ms = params.get("timeoutMs")
            if not run_id:
                return {"id": req_id, "ok": False, "error": {"code": "invalid_request", "message": "runId required"}}
            try:
                timeout_ms_int = int(timeout_ms) if timeout_ms is not None else 30_000
            except Exception:
                timeout_ms_int = 30_000
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
                },
            }

        if method == "health":
            return {"id": req_id, "ok": True, "payload": await health()}

        if method == "ls":
            import os

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

