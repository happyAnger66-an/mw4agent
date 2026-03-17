"""Session store migration helpers.

Goal: migrate legacy single-file session store (e.g. ./mw4agent.sessions.json)
into the new per-agent store layout:
  ~/.mw4agent/agents/<agentId>/sessions/sessions.json

The migration is best-effort, idempotent, and creates a backup of the legacy file.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Tuple

from ..agent_manager import AgentManager
from .manager import SessionManager


def _now_ms() -> int:
    return int(time.time() * 1000)


def migrate_legacy_sessions_file(
    *,
    legacy_path: str,
    agent_id: str = "main",
    agent_manager: Optional[AgentManager] = None,
) -> Tuple[bool, str]:
    """Migrate legacy session store file into per-agent sessions store.

    Returns (migrated, message).
    """
    legacy = Path(legacy_path).expanduser()
    if not legacy.exists() or not legacy.is_file():
        return (False, "legacy sessions file not found")
    if legacy.stat().st_size <= 0:
        return (False, "legacy sessions file is empty")

    mgr = agent_manager or AgentManager()
    target_path = Path(mgr.resolve_sessions_file(agent_id))
    target_path.parent.mkdir(parents=True, exist_ok=True)

    legacy_mgr = SessionManager(str(legacy))
    legacy_sessions = list(legacy_mgr.sessions.values())
    if not legacy_sessions:
        return (False, "legacy sessions file contains no sessions")

    target_mgr = SessionManager(str(target_path))
    existing_ids = set(target_mgr.sessions.keys())
    added = 0
    updated = 0

    for entry in legacy_sessions:
        # Always normalize stored agent_id to requested agent.
        try:
            entry.agent_id = agent_id
        except Exception:
            pass
        if entry.session_id in target_mgr.sessions:
            # Merge conservatively: keep newer updated_at and higher message_count/total_tokens.
            cur = target_mgr.sessions[entry.session_id]
            try:
                if (entry.updated_at or 0) > (cur.updated_at or 0):
                    cur.updated_at = entry.updated_at
                if (entry.message_count or 0) > (cur.message_count or 0):
                    cur.message_count = entry.message_count
                if (entry.total_tokens or 0) > (cur.total_tokens or 0):
                    cur.total_tokens = entry.total_tokens
                if isinstance(entry.metadata, dict):
                    cur.metadata = {**(cur.metadata or {}), **entry.metadata}
                updated += 1
            except Exception:
                # If merge fails, skip rather than corrupt.
                continue
        else:
            target_mgr.sessions[entry.session_id] = entry
            added += 1

    # Persist migrated store.
    target_mgr._save()  # noqa: SLF001 - internal save is the intended persistence hook here

    # Backup the legacy file after successful save.
    ts = _now_ms()
    backup_path = legacy.with_suffix(legacy.suffix + f".bak.{ts}")
    try:
        shutil.copy2(str(legacy), str(backup_path))
    except Exception:
        # Best-effort; migration still considered successful if target was saved.
        pass

    # Mark migration in target store metadata (best-effort).
    try:
        migrated_meta = {
            "migratedFrom": str(legacy),
            "migratedAt": ts,
            "added": added,
            "updated": updated,
        }
        # Attach to every migrated entry metadata minimally (avoid a global header format change).
        for sid, ent in target_mgr.sessions.items():
            if sid in existing_ids:
                continue
            ent.metadata = {**(ent.metadata or {}), "_migration": migrated_meta}
        target_mgr._save()  # persist metadata update
    except Exception:
        pass

    return (
        True,
        f"migrated {len(legacy_sessions)} session(s) into {target_path} (added={added}, updated={updated}); backup={backup_path}",
    )


def auto_migrate_legacy_sessions(agent_manager: AgentManager, *, agent_id: str = "main") -> None:
    """Best-effort auto migration from known legacy paths.

    We attempt to migrate from:
    - ./mw4agent.sessions.json (common dev default)
    - ~/.mw4agent/mw4agent.sessions.json (state-root legacy)
    """
    candidates = []
    try:
        candidates.append(os.path.abspath("mw4agent.sessions.json"))
    except Exception:
        pass
    try:
        state_root = Path(agent_manager.get("main").agent_dir).parent.parent  # ~/.mw4agent
        candidates.append(str(state_root / "mw4agent.sessions.json"))
    except Exception:
        # Fall back to home-based guess
        try:
            candidates.append(str(Path.home() / ".mw4agent" / "mw4agent.sessions.json"))
        except Exception:
            pass

    seen = set()
    for p in candidates:
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            migrated, _msg = migrate_legacy_sessions_file(
                legacy_path=p, agent_id=agent_id, agent_manager=agent_manager
            )
            if migrated:
                # Migrate only once.
                return
        except Exception:
            continue

