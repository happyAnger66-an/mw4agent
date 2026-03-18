"""Multi-agent session manager.

Routes session operations to per-agent SessionManager instances based on agent_id.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..agent_manager import AgentManager
from .manager import SessionEntry, SessionManager
from .migrate import auto_migrate_legacy_sessions


class MultiAgentSessionManager:
    def __init__(self, agent_manager: Optional[AgentManager] = None) -> None:
        self.agent_manager = agent_manager or AgentManager()
        self._cache: Dict[str, SessionManager] = {}
        # Ensure default agent exists.
        self.agent_manager.ensure_main()
        # Best-effort migration from legacy single-store file(s).
        auto_migrate_legacy_sessions(self.agent_manager, agent_id="main")

    def _for_agent(self, agent_id: Optional[str]) -> SessionManager:
        aid = (agent_id or "").strip().lower() or "main"
        if aid not in self._cache:
            session_file = self.agent_manager.resolve_sessions_file(aid)
            self._cache[aid] = SessionManager(session_file)
        return self._cache[aid]

    def get_session(self, session_id: str, *, agent_id: Optional[str] = None) -> Optional[SessionEntry]:
        return self._for_agent(agent_id).get_session(session_id)

    def get_or_create_session(
        self,
        session_id: str,
        session_key: str,
        agent_id: Optional[str] = None,
    ) -> SessionEntry:
        return self._for_agent(agent_id).get_or_create_session(
            session_id=session_id,
            session_key=session_key,
            agent_id=agent_id,
        )

    def update_session(self, session_id: str, *, agent_id: Optional[str] = None, **kwargs) -> None:
        self._for_agent(agent_id).update_session(session_id, **kwargs)

    def list_sessions(self, agent_id: Optional[str] = None) -> List[SessionEntry]:
        # If no agent_id provided, default to main for now (OpenClaw has visibility policies;
        # mw4agent can add cross-agent listing later).
        return self._for_agent(agent_id).list_sessions(agent_id=agent_id)

    def find_latest_by_session_key(self, session_key: str, *, agent_id: Optional[str] = None) -> Optional[SessionEntry]:
        return self._for_agent(agent_id).find_latest_by_session_key(session_key)

    def delete_session(self, session_id: str, *, agent_id: Optional[str] = None) -> bool:
        return self._for_agent(agent_id).delete_session(session_id)

    def resolve_transcript_path(self, session_id: str, *, agent_id: Optional[str] = None) -> str:
        """Resolve per-agent transcript path for this session store."""
        from .transcript import resolve_session_transcript_path

        return resolve_session_transcript_path(agent_id=agent_id, session_id=session_id)

