"""Register `sessions` CLI commands (OpenClaw-inspired)."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import click

from ...agents.agent_manager import AgentManager
from ...agents.session import MultiAgentSessionManager, resolve_session_transcript_path


def _fmt_ts_ms(ts_ms: int) -> str:
    if not ts_ms:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts_ms / 1000.0))
    except Exception:
        return str(ts_ms)


def register_sessions_cli(program: click.Group, _ctx) -> None:
    @program.command(name="sessions", help="List sessions (OpenClaw-compatible)")
    @click.option("--agent", "agent_id", default="", show_default=False, help="Filter by agent id")
    @click.option("--limit", type=int, default=50, show_default=True, help="Max rows")
    @click.option("--json", "json_output", is_flag=True, help="Output JSON")
    def sessions_list(agent_id: str, limit: int, json_output: bool) -> None:
        agent_mgr = AgentManager()
        session_mgr = MultiAgentSessionManager(agent_manager=agent_mgr)

        aid = agent_id.strip() or None
        sessions = session_mgr.list_sessions(agent_id=aid)
        if limit and limit > 0:
            sessions = sessions[:limit]

        rows: list[Dict[str, Any]] = []
        for s in sessions:
            rows.append(
                {
                    "agentId": s.agent_id or agent_id,
                    "sessionId": s.session_id,
                    "sessionKey": s.session_key,
                    "createdAt": s.created_at,
                    "updatedAt": s.updated_at,
                    "messageCount": s.message_count,
                    "totalTokens": s.total_tokens,
                    "transcriptFile": resolve_session_transcript_path(
                        agent_id=s.agent_id or agent_id, session_id=s.session_id
                    ),
                }
            )

        if json_output:
            click.echo(json.dumps({"sessions": rows}, ensure_ascii=False, indent=2))
            return

        if not rows:
            click.echo("(no sessions)")
            return

        # Simple table output (stable for humans).
        title = f"Sessions (agent={aid})" if aid else "Sessions"
        click.echo(title)
        click.echo(f"{'UPDATED':19}  {'SESSION_ID':36}  {'MSG':>4}  SESSION_KEY")
        for r in rows:
            updated = _fmt_ts_ms(int(r.get("updatedAt") or 0))
            sid = str(r.get("sessionId") or "")
            mc = int(r.get("messageCount") or 0)
            sk = str(r.get("sessionKey") or "")
            click.echo(f"{updated:19}  {sid:36}  {mc:4d}  {sk}")

