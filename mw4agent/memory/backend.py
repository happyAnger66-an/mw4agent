"""Memory backend abstraction (Phase 0: stub delegating to file-based search).

This module introduces a minimal MemoryBackend interface and a default
implementation that simply delegates to the existing file-based memory.search /
read_file functions. Later phases can swap this out for a real MemoryIndexManager
with embeddings / hybrid search without changing tools.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .search import (
    MemorySearchResult,
    MemoryReadResult,
    search as file_search_search,
    read_file as file_search_read_file,
)
from .index import index_files, search_index, upsert_chunk
from ..config.root import read_root_section
from ..config.paths import resolve_agent_workspace_dir, resolve_memory_index_db_path


@dataclass
class SearchOptions:
    max_results: int = 10
    min_score: float = 0.0
    session_key: Optional[str] = None
    session_id: Optional[str] = None
    agent_id: Optional[str] = None


class MemoryBackend:
    """Base interface for memory backends.

    Phase 0: only the default stub implementation is used.
    """

    def search(
        self,
        query: str,
        workspace_dir: str,
        *,
        options: SearchOptions,
    ) -> List[MemorySearchResult]:
        raise NotImplementedError

    def read_file(
        self,
        workspace_dir: str,
        rel_path: str,
        *,
        from_line: Optional[int] = None,
        lines: Optional[int] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> MemoryReadResult:
        raise NotImplementedError

    def sync(self, *, reason: str = "manual") -> None:
        """Placeholder for future index sync."""
        return None

    def note_session_delta(
        self,
        *,
        agent_id: Optional[str] = None,
        session_id: str,
        bytes_delta: int = 0,
        messages_delta: int = 0,
        workspace_dir: Optional[str] = None,
    ) -> None:
        """Placeholder for future session delta tracking."""
        return None

    def status(self) -> Dict[str, Any]:
        """Return backend status/diagnostics (stub for now)."""
        return {}


class StubMemoryBackend(MemoryBackend):
    """Phase 0 backend: delegate to existing file-based search/read_file."""

    def search(
        self,
        query: str,
        workspace_dir: str,
        *,
        options: SearchOptions,
    ) -> List[MemorySearchResult]:
        return file_search_search(
            query,
            workspace_dir,
            max_results=options.max_results,
            min_score=options.min_score,
            session_key=options.session_key,
            session_id=options.session_id,
            agent_id=options.agent_id,
        )

    def read_file(
        self,
        workspace_dir: str,
        rel_path: str,
        *,
        from_line: Optional[int] = None,
        lines: Optional[int] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> MemoryReadResult:
        return file_search_read_file(
            workspace_dir,
            rel_path,
            from_line=from_line,
            lines=lines,
            session_id=session_id,
            agent_id=agent_id,
        )


class LocalIndexBackend(MemoryBackend):
    """SQLite MemoryIndex: file memory + optional session transcript chunk (Phase 1–2).

    - Indexes MEMORY.md + memory/*.md (source='memory').
    - Session: one synthetic chunk per session (source='session', path sessions/<id>.jsonl).
    - LIKE-based search; no embeddings yet.
    """

    def __init__(self, *, memory_cfg: Dict[str, Any]) -> None:
        self._memory_cfg = memory_cfg if isinstance(memory_cfg, dict) else {}
        self._indexed_workspaces: set[tuple[str, str]] = set()  # (agent_id, workspace_dir)
        self._indexed_session_mtime: Dict[tuple[str, str, str], float] = {}  # (agent_id, session_id, db_path) -> mtime
        # Accumulated deltas for memory.sync.sessions thresholds (Phase 2).
        self._delta_acc: Dict[tuple[str, str, str], Dict[str, int]] = {}

    def _db_path_for(
        self, agent_id: Optional[str], workspace_dir: Optional[str] = None
    ) -> str:
        aid = (agent_id or "").strip().lower() or "main"
        ws = (workspace_dir or "").strip() or resolve_agent_workspace_dir(aid)
        return resolve_memory_index_db_path(aid, ws)

    def _session_sync_thresholds(self) -> Tuple[int, int]:
        """Return (delta_bytes, delta_messages). Both 0 => sync on every note (default)."""
        sync = self._memory_cfg.get("sync")
        if not isinstance(sync, dict):
            return (0, 0)
        sess = sync.get("sessions")
        if not isinstance(sess, dict):
            return (0, 0)
        try:
            db = int(sess["deltaBytes"]) if sess.get("deltaBytes") is not None else 0
        except (TypeError, ValueError):
            db = 0
        try:
            dm = int(sess["deltaMessages"]) if sess.get("deltaMessages") is not None else 0
        except (TypeError, ValueError):
            dm = 0
        return (max(0, db), max(0, dm))

    def _ensure_index(self, *, agent_id: Optional[str], workspace_dir: str) -> str:
        key = ((agent_id or "").strip().lower() or "main", str(Path(workspace_dir).resolve()))
        db_path = self._db_path_for(agent_id, workspace_dir)
        if key not in self._indexed_workspaces:
            index_files(db_path=db_path, workspace_dir=workspace_dir, sources=("memory",))
            self._indexed_workspaces.add(key)
        return db_path

    def _ensure_session_index(
        self,
        *,
        db_path: str,
        agent_id: Optional[str],
        session_id: Optional[str],
    ) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        # Phase 2: index the transcript (leaf chain) as one synthetic chunk.
        try:
            from ..agents.session.transcript import build_messages_from_leaf, resolve_session_transcript_path
        except Exception:
            return
        transcript_file = resolve_session_transcript_path(agent_id=agent_id, session_id=sid)
        try:
            mtime = os.path.getmtime(transcript_file)
        except OSError:
            return
        aid = (agent_id or "").strip().lower() or "main"
        k = (aid, sid, db_path)
        if self._indexed_session_mtime.get(k) == mtime:
            return
        msgs = build_messages_from_leaf(transcript_file=transcript_file, limit=200)
        parts: List[str] = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "").strip()
            if role not in ("user", "assistant", "system"):
                continue
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(f"{role}: {content.strip()}")
        if not parts:
            return
        blob = "\n".join(parts)
        path = f"sessions/{sid}.jsonl"
        upsert_chunk(db_path=db_path, source="session", path=path, content=blob)
        self._indexed_session_mtime[k] = mtime

    def note_session_delta(
        self,
        *,
        agent_id: Optional[str] = None,
        session_id: str,
        bytes_delta: int = 0,
        messages_delta: int = 0,
        workspace_dir: Optional[str] = None,
    ) -> None:
        """Refresh session chunk in the index (eager or after sync.sessions thresholds)."""
        sid = (session_id or "").strip()
        if not sid:
            return
        aid = (agent_id or "").strip().lower() or "main"
        ws = (workspace_dir or "").strip() or resolve_agent_workspace_dir(aid)
        db_path = self._db_path_for(agent_id, ws)
        key = (aid, sid, db_path)
        db_th, dm_th = self._session_sync_thresholds()
        if db_th <= 0 and dm_th <= 0:
            self._ensure_session_index(db_path=db_path, agent_id=agent_id, session_id=sid)
            return
        acc = self._delta_acc.setdefault(key, {"bytes": 0, "messages": 0})
        acc["bytes"] += max(0, int(bytes_delta))
        acc["messages"] += max(0, int(messages_delta))
        fire = (db_th > 0 and acc["bytes"] >= db_th) or (dm_th > 0 and acc["messages"] >= dm_th)
        if not fire:
            return
        acc["bytes"] = 0
        acc["messages"] = 0
        self._indexed_session_mtime.pop(key, None)
        self._ensure_session_index(db_path=db_path, agent_id=agent_id, session_id=sid)

    def search(
        self,
        query: str,
        workspace_dir: str,
        *,
        options: SearchOptions,
    ) -> List[MemorySearchResult]:
        db_path = self._ensure_index(agent_id=options.agent_id, workspace_dir=workspace_dir)
        if options.session_id:
            self._ensure_session_index(db_path=db_path, agent_id=options.agent_id, session_id=options.session_id)
        rows = search_index(
            db_path=db_path,
            query=query,
            max_results=options.max_results,
            min_score=options.min_score,
            sources=("memory", "session") if options.session_id else ("memory",),
        )
        out: List[MemorySearchResult] = []
        for row in rows:
            raw_sid = row.get("session_id")
            sess_id: Optional[str] = None
            if isinstance(raw_sid, str) and raw_sid.strip():
                sess_id = raw_sid.strip()
            elif raw_sid is not None:
                s2 = str(raw_sid).strip()
                sess_id = s2 or None
            cr = row.get("created_at")
            up = row.get("updated_at")
            out.append(
                MemorySearchResult(
                    path=str(row.get("path") or ""),
                    start_line=int(row.get("start_line") or 1),
                    end_line=int(row.get("end_line") or 1),
                    score=float(row.get("score") or 1.0),
                    snippet=str(row.get("snippet") or ""),
                    source=str(row.get("source") or "memory") or "memory",
                    session_id=sess_id,
                    created_at=int(cr) if cr is not None else None,
                    updated_at=int(up) if up is not None else None,
                )
            )
        return out

    def read_file(
        self,
        workspace_dir: str,
        rel_path: str,
        *,
        from_line: Optional[int] = None,
        lines: Optional[int] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> MemoryReadResult:
        # For Phase 1, delegate actual file reading to the existing implementation.
        return file_search_read_file(
            workspace_dir,
            rel_path,
            from_line=from_line,
            lines=lines,
            session_id=session_id,
            agent_id=agent_id,
        )

    def sync(self, *, reason: str = "manual") -> None:
        """Invalidate in-memory index caches so the next search rebuilds from disk.

        Does not require workspace_dir (per-interface); each workspace is re-indexed on demand.
        """
        _ = reason
        self._indexed_workspaces.clear()
        self._indexed_session_mtime.clear()
        self._delta_acc.clear()


_default_backend: Optional[MemoryBackend] = None


def reset_memory_backend_singleton() -> None:
    """Clear the process-wide MemoryBackend singleton (for tests and config reload)."""
    global _default_backend
    _default_backend = None


def get_memory_backend() -> MemoryBackend:
    """Return the process-wide MemoryBackend instance.

    Selection logic (Phase 1):
    - If root config section "memory" has enabled=true, use LocalIndexBackend (SQLite index).
    - Otherwise fall back to StubMemoryBackend (file-based search).
    """
    global _default_backend
    if _default_backend is None:
        cfg = read_root_section("memory", default={})
        enabled = False
        if isinstance(cfg, dict):
            val = cfg.get("enabled")
            if isinstance(val, bool):
                enabled = val
        if enabled:
            _default_backend = LocalIndexBackend(memory_cfg=cfg)
        else:
            _default_backend = StubMemoryBackend()
    return _default_backend

