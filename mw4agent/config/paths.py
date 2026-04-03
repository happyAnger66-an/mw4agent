"""Paths for workspace and state (OpenClaw-style).

mw4agent supports multi-agent state under:
  ~/.mw4agent/agents/<agentId>/

Each agent can have its own:
- agent_dir (state root for that agent)
- workspace_dir (default workspace root for tools)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


DEFAULT_AGENT_ID = "main"


def get_state_dir() -> str:
    """Return mw4agent state dir (~/.mw4agent) with env override."""
    env = os.environ.get("MW4AGENT_STATE_DIR")
    if env and env.strip():
        return os.path.abspath(env.strip())
    return str(Path.home() / ".mw4agent")


def get_agents_root_dir() -> str:
    """Return agents root dir (~/.mw4agent/agents)."""
    return os.path.join(get_state_dir(), "agents")


def normalize_agent_id(agent_id: Optional[str]) -> str:
    v = (agent_id or "").strip().lower()
    return v or DEFAULT_AGENT_ID


def resolve_agent_dir(agent_id: Optional[str]) -> str:
    """Return agent directory (~/.mw4agent/agents/<agentId>)."""
    aid = normalize_agent_id(agent_id)
    return os.path.join(get_agents_root_dir(), aid)


def resolve_agent_workspace_dir(agent_id: Optional[str]) -> str:
    """Return default per-agent workspace directory (~/.mw4agent/agents/<agentId>/workspace)."""
    env = os.environ.get("MW4AGENT_WORKSPACE_DIR")
    if env and env.strip():
        # Global override (applies to all agents).
        return os.path.abspath(env.strip())
    return os.path.join(resolve_agent_dir(agent_id), "workspace")


def resolve_agent_sessions_file(agent_id: Optional[str]) -> str:
    """Return per-agent sessions store path (~/.mw4agent/agents/<agentId>/sessions/sessions.json)."""
    return os.path.join(resolve_agent_dir(agent_id), "sessions", "sessions.json")


def get_default_workspace_dir() -> str:
    """Backward-compatible: default workspace directory for main agent.

    Override with MW4AGENT_WORKSPACE_DIR env. Aligns with OpenClaw's
    ~/.openclaw/workspace.
    """
    return resolve_agent_workspace_dir(DEFAULT_AGENT_ID)


def ensure_workspace_dir() -> str:
    """Ensure default (main) workspace dir exists; return its path."""
    path = get_default_workspace_dir()
    os.makedirs(path, exist_ok=True)
    return path


def ensure_agent_dirs(agent_id: Optional[str]) -> tuple[str, str, str]:
    """Ensure agent_dir, workspace_dir and sessions dir exist."""
    aid = normalize_agent_id(agent_id)
    agent_dir = resolve_agent_dir(aid)
    workspace_dir = resolve_agent_workspace_dir(aid)
    sessions_dir = os.path.join(agent_dir, "sessions")
    os.makedirs(agent_dir, exist_ok=True)
    os.makedirs(workspace_dir, exist_ok=True)
    os.makedirs(sessions_dir, exist_ok=True)
    return agent_dir, workspace_dir, sessions_dir


def orchestrations_root_dir() -> str:
    """Gateway orchestration state root: ``<state>/orchestrations``."""
    return os.path.join(get_state_dir(), "orchestrations")


def orchestration_state_dir(orch_id: str) -> str:
    """Single orchestration directory: ``<state>/orchestrations/<orchId>`` (``orch.json``, team ``AGENTS.md``, …)."""
    oid = (orch_id or "").strip()
    if not oid:
        raise ValueError("orch_id is required")
    return os.path.join(orchestrations_root_dir(), oid)


def resolve_orchestration_agent_workspace_dir(orch_id: str, agent_id: Optional[str]) -> str:
    """Per-orchestration per-agent workspace (MEMORY.md, tools cwd, memory index scope).

    Layout: ``<state>/orchestrations/<orchId>/agents/<agentId>/workspace``
    """
    oid = (orch_id or "").strip() or "default"
    aid = normalize_agent_id(agent_id)
    return os.path.join(orchestrations_root_dir(), oid, "agents", aid, "workspace")


def resolve_memory_index_db_path(agent_id: Optional[str], workspace_dir: str) -> str:
    """SQLite path for :class:`~mw4agent.memory.backend.LocalIndexBackend`.

    - Default agent workspace → ``<agent_dir>/memory/index.sqlite`` (legacy).
    - Orchestration workspace → ``.../orchestrations/<id>/agents/<aid>/memory/index.sqlite``.
    - Any other custom workspace → ``<workspace>/.mw4agent_memory/index.sqlite``.

    ``index_files`` rebuilds the ``memory`` source for one workspace per DB; separate DBs
    avoid cross-workspace deletes in a shared file.
    """
    aid = normalize_agent_id(agent_id)
    ws = Path(workspace_dir).resolve()
    default_ws = Path(resolve_agent_workspace_dir(aid)).resolve()
    if ws == default_ws:
        p = Path(resolve_agent_dir(aid)) / "memory" / "index.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    orch_root = (Path(get_state_dir()).resolve() / "orchestrations")
    try:
        rel = ws.relative_to(orch_root)
    except ValueError:
        p = ws / ".mw4agent_memory" / "index.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    parts = rel.parts
    if (
        len(parts) >= 4
        and parts[1] == "agents"
        and parts[3] == "workspace"
        and normalize_agent_id(parts[2]) == aid
    ):
        base = orch_root / parts[0] / "agents" / parts[2]
        p = base / "memory" / "index.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    p = ws / ".mw4agent_memory" / "index.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)
