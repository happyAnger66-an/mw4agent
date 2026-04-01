"""Session Manager - manages agent sessions.

加密适配：
- 原先直接以 JSON 形式明文写入磁盘；
- 现在改为优先使用 `EncryptedFileStore` 进行加密读写；
- 为了平滑迁移，若文件不是加密格式，可按明文 JSON 读入并在下一次保存时写成加密格式。
"""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

from ...crypto import EncryptionConfigError, get_default_encrypted_store, is_encryption_enabled  # type: ignore[attr-defined]
from .transcript import validate_session_id


def _normalize_epoch_ms(ts: int, *, now_ms: int) -> int:
    """Normalize unknown timestamp units to epoch milliseconds.

    - If ts looks like epoch seconds (>= 1e9 and < 1e12), convert to ms.
    - If ts is implausibly small (< 1e9), treat it as invalid and use now_ms.
    - Otherwise assume it's already epoch milliseconds.
    """
    if not isinstance(ts, int):
        return now_ms
    if ts <= 0:
        return now_ms
    if ts < 1_000_000_000:
        # Too small to be a real epoch timestamp; likely test/legacy placeholder.
        return now_ms
    if ts < 1_000_000_000_000:
        # Epoch seconds range for modern dates.
        return ts * 1000
    return ts


@dataclass
class SessionEntry:
    """Session entry - similar to OpenClaw's SessionEntry"""

    session_id: str
    session_key: str
    agent_id: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0
    message_count: int = 0
    total_tokens: int = 0
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        now_ms = int(time.time() * 1000)
        if self.metadata is None:
            self.metadata = {}
        if self.created_at == 0:
            self.created_at = now_ms
        if self.updated_at == 0:
            self.updated_at = self.created_at

        # Back-compat: normalize legacy seconds timestamps / bad small values.
        self.created_at = _normalize_epoch_ms(int(self.created_at), now_ms=now_ms)
        self.updated_at = _normalize_epoch_ms(int(self.updated_at), now_ms=now_ms)


class SessionManager:
    """Manages agent sessions - similar to OpenClaw's SessionManager"""

    def __init__(self, session_file: str):
        """
        Args:
            session_file: Path to session file (JSON or encrypted JSON)
        """
        self.session_file = Path(session_file)
        self.sessions: Dict[str, SessionEntry] = {}
        self._load()

    def _load(self) -> None:
        """Load sessions from file (encrypted first, fallback to plaintext)."""
        if not self.session_file.exists():
            return
        data = None
        if is_encryption_enabled():
            try:
                store = get_default_encrypted_store()
                data = store.read_json(str(self.session_file), fallback_plaintext=True)
            except EncryptionConfigError as e:
                # Encryption explicitly enabled but misconfigured; fall back.
                print(f"Warning: Encryption not configured, falling back to plaintext: {e}")
            except Exception as e:
                print(f"Warning: Failed to load sessions (encrypted path): {e}")
                return
        if data is None:
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:  # pragma: no cover - 容错路径
                print(f"Warning: Failed to load sessions (plaintext fallback): {e}")
                return

        if isinstance(data, dict) and "sessions" in data:
            for session_data in data["sessions"]:
                try:
                    entry = SessionEntry(**session_data)
                except TypeError:
                    continue
                self.sessions[entry.session_id] = entry

    def _save(self) -> None:
        """Save sessions to file (prefer encrypted; fallback to plaintext)."""
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "sessions": [asdict(entry) for entry in self.sessions.values()],
            }
            if is_encryption_enabled():
                try:
                    store = get_default_encrypted_store()
                    store.write_json(str(self.session_file), payload)
                    return
                except EncryptionConfigError as e:
                    print(f"Warning: Encryption not configured, writing plaintext sessions: {e}")
                except Exception as e:
                    print(f"Warning: Failed to save sessions (encrypted path): {e}")
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: Failed to save sessions: {e}")

    def get_session(self, session_id: str) -> Optional[SessionEntry]:
        """Get session by ID"""
        return self.sessions.get(session_id)

    def get_or_create_session(
        self,
        session_id: str,
        session_key: str,
        agent_id: Optional[str] = None,
    ) -> SessionEntry:
        """Get or create a session"""
        if session_id in self.sessions:
            entry = self.sessions[session_id]
            entry.updated_at = int(time.time() * 1000)
            self._save()
            return entry

        entry = SessionEntry(
            session_id=session_id,
            session_key=session_key,
            agent_id=agent_id,
        )
        self.sessions[session_id] = entry
        self._save()
        return entry

    def update_session(self, session_id: str, **kwargs) -> None:
        """Update session metadata"""
        if session_id not in self.sessions:
            return
        entry = self.sessions[session_id]
        entry.updated_at = int(time.time() * 1000)
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        self._save()

    def list_sessions(self, agent_id: Optional[str] = None) -> List[SessionEntry]:
        """List all sessions, optionally filtered by agent_id"""
        sessions = list(self.sessions.values())
        if agent_id:
            sessions = [s for s in sessions if s.agent_id == agent_id]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def find_latest_by_session_key(self, session_key: str) -> Optional[SessionEntry]:
        """Return the most recently updated session for a session_key."""
        key = (session_key or "").strip()
        if not key:
            return None
        best: Optional[SessionEntry] = None
        for entry in self.sessions.values():
            if entry.session_key != key:
                continue
            if best is None:
                best = entry
                continue
            # Tie-break deterministically: updated_at -> created_at -> session_id.
            a = (int(entry.updated_at or 0), int(entry.created_at or 0), str(entry.session_id or ""))
            b = (int(best.updated_at or 0), int(best.created_at or 0), str(best.session_id or ""))
            if a > b:
                best = entry
        return best

    def delete_session(self, session_id: str) -> bool:
        """Delete a session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            self._save()
            return True
        return False

    def resolve_transcript_path(self, session_id: str) -> str:
        """Resolve transcript path colocated with this session store.

        When running the gateway with --session-file (single-store mode), we keep
        transcripts next to that store file to avoid splitting state between
        ~/.mw4agent and the provided session_file directory.
        """
        sid = validate_session_id(session_id)
        return str(self.session_file.parent / f"{sid}.jsonl")
