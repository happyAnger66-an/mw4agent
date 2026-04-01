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
    # Optional per-agent skill allowlist (merged with global skills.filter by intersection).
    # If present and empty, the agent sees no skills.
    skills: Optional[List[str]] = None
    # Optional UI avatar: basename only, resolved as ``/icons/headers/<avatar>`` in desktop.
    avatar: Optional[str] = None

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


def normalize_avatar_basename(raw: Optional[str]) -> Optional[str]:
    """Single safe filename for ``public/icons/headers/<name>`` (no path segments)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if any(ch in s for ch in ("/", "\\", "\x00")) or ".." in s:
        raise ValueError("avatar must be a plain filename (no paths)")
    base = os.path.basename(s.replace("\\", "/"))
    if base != s:
        raise ValueError("avatar must be a plain filename (no paths)")
    if len(base) > 200:
        raise ValueError("avatar filename too long")
    return base


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
        skills_raw = data.get("skills")
        skills: Optional[List[str]] = None
        if isinstance(skills_raw, list):
            skills = [str(x).strip() for x in skills_raw if str(x).strip()]
        av = data.get("avatar")
        avatar = str(av).strip() if isinstance(av, str) and str(av).strip() else None
        return AgentConfig(
            agent_id=aid,
            agent_dir=os.path.abspath(agent_dir),
            workspace_dir=os.path.abspath(workspace_dir),
            created_at=created_at,
            updated_at=updated_at,
            metadata=meta,
            llm=llm,
            skills=skills,
            avatar=avatar,
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

    def create_agent(
        self,
        agent_id: str,
        *,
        workspace_dir: Optional[str] = None,
        llm: Optional[Dict[str, Any]] = None,
        avatar: Optional[str] = None,
    ) -> AgentConfig:
        """Create a new agent and write ``agent.json``. Raises if the agent already exists."""
        raw = (agent_id or "").strip()
        if not raw:
            raise ValueError("agent_id is required")
        for bad in ("/", "\\", "\x00"):
            if bad in raw:
                raise ValueError(f"agent_id must not contain {bad!r}")

        aid = normalize_agent_id(raw)
        if self.get(aid) is not None:
            raise ValueError(f"agent already exists: {aid}")

        agent_dir = resolve_agent_dir(aid)
        sessions_parent = os.path.dirname(resolve_agent_sessions_file(aid))
        resolved_workspace: str
        if workspace_dir and str(workspace_dir).strip():
            resolved_workspace = os.path.abspath(os.path.expanduser(str(workspace_dir).strip()))
        else:
            resolved_workspace = os.path.abspath(resolve_agent_workspace_dir(aid))

        os.makedirs(agent_dir, exist_ok=True)
        os.makedirs(sessions_parent, exist_ok=True)
        os.makedirs(resolved_workspace, exist_ok=True)

        llm_clean: Optional[Dict[str, Any]] = None
        if isinstance(llm, dict) and llm:
            allowed_map = {
                "provider": "provider",
                "model": "model",
                "model_id": "model",
                "base_url": "base_url",
                "baseUrl": "base_url",
                "api_key": "api_key",
                "apiKey": "api_key",
                "thinking_level": "thinking_level",
                "thinkingLevel": "thinking_level",
            }
            tmp: Dict[str, str] = {}
            for k, nk in allowed_map.items():
                if k not in llm:
                    continue
                v = llm.get(k)
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    tmp[nk] = s
            llm_clean = tmp or None

        avatar_clean: Optional[str] = None
        if avatar is not None and str(avatar).strip():
            avatar_clean = normalize_avatar_basename(str(avatar))

        now = int(time.time() * 1000)
        cfg = AgentConfig(
            agent_id=aid,
            agent_dir=os.path.abspath(agent_dir),
            workspace_dir=resolved_workspace,
            created_at=now,
            updated_at=now,
            metadata={},
            llm=llm_clean,
            avatar=avatar_clean,
        )
        self.save(cfg)
        return cfg

    def set_avatar(self, agent_id: str, avatar: Optional[str] = None) -> AgentConfig:
        """Update ``avatar`` on ``agent.json`` (basename only; empty clears)."""
        aid = normalize_agent_id(agent_id)
        cfg = self.get(aid)
        if cfg is None:
            cfg = self.get_or_create(aid)
        raw = (avatar if avatar is not None else "").strip()
        if not raw:
            cfg.avatar = None
        else:
            cfg.avatar = normalize_avatar_basename(raw)
        self.save(cfg)
        return cfg

    def update_llm(self, agent_id: str, llm: Optional[Dict[str, Any]]) -> AgentConfig:
        """Merge ``llm`` fields into ``agent.json`` (empty string clears a field).

        Keys not present in ``llm`` are left unchanged. ``api_key`` is only updated
        when a non-empty value is supplied (omit the key to keep the stored key).
        """
        aid = normalize_agent_id(agent_id)
        cfg = self.get(aid)
        if cfg is None:
            cfg = self.get_or_create(aid)
        base: Dict[str, str] = dict(cfg.llm or {})
        if not isinstance(llm, dict) or not llm:
            return cfg
        allowed_map = {
            "provider": "provider",
            "model": "model",
            "model_id": "model",
            "base_url": "base_url",
            "baseUrl": "base_url",
            "api_key": "api_key",
            "apiKey": "api_key",
            "thinking_level": "thinking_level",
            "thinkingLevel": "thinking_level",
        }
        for k, v in llm.items():
            if k not in allowed_map:
                continue
            nk = allowed_map[k]
            if nk == "api_key":
                if v is None:
                    continue
                s = str(v).strip()
                if not s:
                    continue
                base[nk] = s
                continue
            if v is None:
                base.pop(nk, None)
                continue
            s = str(v).strip()
            if not s:
                base.pop(nk, None)
            else:
                base[nk] = s
        cfg.llm = base or None
        self.save(cfg)
        return cfg

    def update_skills(self, agent_id: str, skills: Optional[Any]) -> AgentConfig:
        """Set per-agent ``skills`` allowlist in ``agent.json``.

        - ``None``: remove the key override (agent inherits global-only filtering semantics).
        - ``[]``: explicit empty allowlist (agent sees no skills in prompt).
        - non-empty ``list``: intersection with global ``skills.filter`` is applied at runtime (plan B).
        """
        aid = normalize_agent_id(agent_id)
        cfg = self.get(aid)
        if cfg is None:
            cfg = self.get_or_create(aid)
        if skills is None:
            cfg.skills = None
        elif isinstance(skills, list):
            cleaned = [str(x).strip() for x in skills if str(x).strip()]
            cfg.skills = cleaned
        else:
            raise ValueError("skills must be null or a list of strings")
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

