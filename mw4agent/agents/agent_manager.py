"""Multi-agent management for mw4agent.

Aligned to OpenClaw's multi-agent state layout, but kept intentionally small:
- Default agent: "main"
- Each agent has an agent_dir under ~/.mw4agent/agents/<agentId>
- Each agent has its own workspace_dir and sessions store under agent_dir
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config.paths import (
    DEFAULT_AGENT_ID,
    ensure_agent_dirs,
    get_agents_root_dir,
    normalize_agent_id,
    resolve_agent_dir,
    resolve_agent_sessions_file,
    resolve_agent_workspace_dir,
)


@dataclass
class AgentConfig:
    agent_id: str
    agent_dir: str
    workspace_dir: str
    created_at: int = 0
    updated_at: int = 0
    metadata: Optional[Dict[str, Any]] = None
    # Optional per-agent LLM overrides (merged with global ~/.mw4agent/mw4agent.json llm section).
    llm: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        now = int(time.time() * 1000)
        if self.created_at == 0:
            self.created_at = now
        if self.updated_at == 0:
            self.updated_at = self.created_at
        if self.metadata is None:
            self.metadata = {}


def _agent_config_path(agent_id: str) -> Path:
    return Path(resolve_agent_dir(agent_id)) / "agent.json"


class AgentManager:
    def __init__(self) -> None:
        self.root = Path(get_agents_root_dir())

    def ensure_main(self) -> AgentConfig:
        return self.get_or_create(DEFAULT_AGENT_ID)

    def list_agents(self) -> List[str]:
        if not self.root.exists():
            return [DEFAULT_AGENT_ID]
        ids: List[str] = []
        for p in self.root.iterdir():
            if not p.is_dir():
                continue
            ids.append(p.name)
        ids = sorted(set([normalize_agent_id(x) for x in ids if x.strip()]))
        return ids or [DEFAULT_AGENT_ID]

    def get(self, agent_id: str) -> Optional[AgentConfig]:
        aid = normalize_agent_id(agent_id)
        cfg_path = _agent_config_path(aid)
        if not cfg_path.exists():
            return None
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        agent_dir = str(data.get("agent_dir") or data.get("agentDir") or resolve_agent_dir(aid))
        workspace_dir = str(
            data.get("workspace_dir") or data.get("workspaceDir") or resolve_agent_workspace_dir(aid)
        )
        created_at = int(data.get("created_at") or data.get("createdAt") or 0) or 0
        updated_at = int(data.get("updated_at") or data.get("updatedAt") or 0) or 0
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else None
        llm_raw = data.get("llm")
        llm = dict(llm_raw) if isinstance(llm_raw, dict) else None
        return AgentConfig(
            agent_id=aid,
            agent_dir=os.path.abspath(agent_dir),
            workspace_dir=os.path.abspath(workspace_dir),
            created_at=created_at,
            updated_at=updated_at,
            metadata=meta,
            llm=llm,
        )

    def get_or_create(
        self,
        agent_id: str,
        *,
        agent_dir: Optional[str] = None,
        workspace_dir: Optional[str] = None,
    ) -> AgentConfig:
        aid = normalize_agent_id(agent_id)
        existing = self.get(aid)
        if existing is not None:
            return existing

        # Create directories.
        ensure_agent_dirs(aid)
        resolved_agent_dir = os.path.abspath(agent_dir) if agent_dir else resolve_agent_dir(aid)
        resolved_workspace_dir = (
            os.path.abspath(workspace_dir) if workspace_dir else resolve_agent_workspace_dir(aid)
        )
        os.makedirs(resolved_agent_dir, exist_ok=True)
        os.makedirs(resolved_workspace_dir, exist_ok=True)
        os.makedirs(os.path.dirname(resolve_agent_sessions_file(aid)), exist_ok=True)

        now = int(time.time() * 1000)
        cfg = AgentConfig(
            agent_id=aid,
            agent_dir=resolved_agent_dir,
            workspace_dir=resolved_workspace_dir,
            created_at=now,
            updated_at=now,
            metadata={},
            llm=None,
        )
        self.save(cfg)
        return cfg

    def save(self, cfg: AgentConfig) -> None:
        aid = normalize_agent_id(cfg.agent_id)
        now = int(time.time() * 1000)
        cfg.updated_at = now
        cfg_path = _agent_config_path(aid)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(cfg)
        cfg_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def resolve_workspace_dir(self, agent_id: Optional[str]) -> str:
        aid = normalize_agent_id(agent_id)
        cfg = self.get(aid)
        return cfg.workspace_dir if cfg else resolve_agent_workspace_dir(aid)

    def resolve_sessions_file(self, agent_id: Optional[str]) -> str:
        aid = normalize_agent_id(agent_id)
        # Creating the agent on-demand ensures per-agent session stores are always available.
        self.get_or_create(aid)
        return resolve_agent_sessions_file(aid)

    def delete(self, agent_id: str, *, allow_main: bool = False) -> bool:
        """Remove ``~/.mw4agent/agents/<agentId>/`` and all contents (sessions, workspace, etc.).

        Only deletes the canonical per-agent directory under the agents root (never follows a
        custom ``agent_dir`` path from ``agent.json`` that may point outside).

        Args:
            agent_id: Target agent id (normalized like other APIs).
            allow_main: If False (default), refuses to delete the default ``main`` agent.

        Returns:
            True if a directory existed and was removed, False if nothing was on disk.

        Raises:
            ValueError: If deleting ``main`` without ``allow_main``, or path safety checks fail.
        """
        aid = normalize_agent_id(agent_id)
        if aid == DEFAULT_AGENT_ID and not allow_main:
            raise ValueError(
                "refusing to delete default agent 'main' (use --force if you really mean it)"
            )

        root_resolved = self.root.resolve()
        agent_path = Path(resolve_agent_dir(aid)).resolve()

        if agent_path.parent != root_resolved:
            raise ValueError(f"refusing to delete: agent path not a direct child of agents root: {agent_path}")
        if agent_path.name != aid:
            raise ValueError("refusing to delete: agent directory name does not match agent id")

        if not agent_path.exists():
            return False
        if not agent_path.is_dir():
            raise ValueError(f"refusing to delete: not a directory: {agent_path}")

        shutil.rmtree(agent_path)
        return True

