"""In-memory Gateway state: dedupe + run registry + WS subscribers."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple

from .node_registry import NodeRegistry
from .types import AgentEvent


@dataclass
class DedupeEntry:
    ts_ms: int
    ok: bool
    payload: Dict[str, Any]
    error: Optional[Dict[str, Any]] = None


@dataclass
class RunSnapshot:
    run_id: str
    status: str  # ok|error|timeout
    started_at: Optional[int] = None
    ended_at: Optional[int] = None
    error: Optional[str] = None
    reply_text: Optional[str] = None  # Accumulated assistant reply text
    stop_reason: Optional[str] = None  # e.g. max_tool_rounds from AgentRunMeta.stop_reason


@dataclass
class RunRecord:
    run_id: str
    session_key: str
    created_at_ms: int
    agent_id: Optional[str] = None
    started_at_ms: Optional[int] = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    snapshot: Optional[RunSnapshot] = None
    seq: int = 0
    reply_text_buffer: str = ""  # Buffer for accumulating assistant reply text


class GatewayState:
    def __init__(self, *, node_token: Optional[str] = None) -> None:
        self.dedupe: Dict[str, DedupeEntry] = {}
        self.runs: Dict[str, RunRecord] = {}
        self.ws_clients: Set[asyncio.Queue] = set()
        self.node_registry: NodeRegistry = NodeRegistry()
        # Token required for node connect when set; when None, node connect is allowed without auth (dev).
        self.node_token: Optional[str] = node_token

    def new_run_id(self) -> str:
        return str(uuid.uuid4())

    def set_dedupe(self, key: str, entry: DedupeEntry) -> None:
        self.dedupe[key] = entry

    def get_dedupe(self, key: str) -> Optional[DedupeEntry]:
        return self.dedupe.get(key)

    def ensure_run(
        self,
        *,
        run_id: str,
        session_key: str,
        agent_id: Optional[str] = None,
    ) -> RunRecord:
        rec = self.runs.get(run_id)
        if rec:
            if agent_id and not rec.agent_id:
                rec.agent_id = agent_id
            return rec
        rec = RunRecord(
            run_id=run_id,
            session_key=session_key,
            created_at_ms=int(time.time() * 1000),
            agent_id=agent_id,
        )
        self.runs[run_id] = rec
        return rec

    def mark_run_terminal(self, run_id: str, snapshot: RunSnapshot) -> None:
        rec = self.runs.get(run_id)
        if not rec:
            rec = self.ensure_run(run_id=run_id, session_key=snapshot.run_id)
        rec.snapshot = snapshot
        rec.done.set()

    async def register_ws(self) -> Tuple[asyncio.Queue, callable]:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.ws_clients.add(q)

        def unregister() -> None:
            self.ws_clients.discard(q)

        return q, unregister

    async def broadcast(self, evt: AgentEvent) -> None:
        # Stamp ts if missing
        if evt.ts == 0:
            evt.ts = int(time.time() * 1000)
        # Fan out best-effort
        dead: Set[asyncio.Queue] = set()
        for q in list(self.ws_clients):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                # Drop slow consumer
                dead.add(q)
        for q in dead:
            self.ws_clients.discard(q)

