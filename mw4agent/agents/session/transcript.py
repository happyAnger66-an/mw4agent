"""Session transcript storage (OpenClaw-style, simplified).

We store short-term session memory (conversation history) as JSONL:

- First line: session header
- Subsequent lines: message records, each contains a `message` object compatible
  with OpenAI Chat Completions `messages` entries (role/content/tool_calls/...).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ...config.paths import ensure_agent_dirs, normalize_agent_id, resolve_agent_dir


SAFE_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def validate_session_id(session_id: str) -> str:
    sid = (session_id or "").strip()
    if not sid or not SAFE_SESSION_ID_RE.match(sid):
        raise ValueError(f"Invalid session_id: {session_id}")
    return sid


def resolve_session_transcript_path(*, agent_id: Optional[str], session_id: str) -> str:
    aid = normalize_agent_id(agent_id)
    sid = validate_session_id(session_id)
    ensure_agent_dirs(aid)
    return os.path.join(resolve_agent_dir(aid), "sessions", f"{sid}.jsonl")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_session_header(*, transcript_file: str, session_id: str, cwd: str) -> None:
    path = Path(transcript_file)
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "type": "session",
        "version": 1,
        "id": validate_session_id(session_id),
        "timestamp": _iso_now(),
        "cwd": cwd,
    }
    path.write_text(json.dumps(header, ensure_ascii=False) + "\n", encoding="utf-8")


def append_messages(
    *,
    transcript_file: str,
    session_id: str,
    cwd: str,
    messages: Iterable[Dict[str, Any]],
) -> None:
    ensure_session_header(transcript_file=transcript_file, session_id=session_id, cwd=cwd)
    path = Path(transcript_file)
    with path.open("a", encoding="utf-8") as f:
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").strip()
            if not role:
                continue
            record = {"type": "message", "timestamp": _now_ms(), "message": msg}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_messages(
    *,
    transcript_file: str,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    path = Path(transcript_file)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("message"), dict):
            out.append(obj["message"])
    if limit is not None and limit > 0 and len(out) > limit:
        return out[-limit:]
    return out


def drop_trailing_orphan_user(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not messages:
        return messages
    last = messages[-1]
    if isinstance(last, dict) and last.get("role") == "user":
        return messages[:-1]
    return messages


def limit_history_user_turns(messages: List[Dict[str, Any]], limit_turns: Optional[int]) -> List[Dict[str, Any]]:
    if not limit_turns or limit_turns <= 0:
        return messages
    user_count = 0
    last_user_index = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_count += 1
            if user_count > limit_turns:
                return messages[last_user_index:]
            last_user_index = i
    return messages


@dataclass
class SessionHistoryConfig:
    history_limit_turns: Optional[int] = None


def resolve_history_limit_turns(
    *,
    cfg: Optional[Dict[str, Any]],
    session_key: Optional[str],
) -> Optional[int]:
    # Minimal compatibility: env override + root config.
    raw_env = os.environ.get("MW4AGENT_HISTORY_LIMIT_TURNS")
    if raw_env and raw_env.strip().isdigit():
        v = int(raw_env.strip())
        return v if v > 0 else None

    if not cfg or not isinstance(cfg, dict):
        return None
    section = cfg.get("session") if isinstance(cfg.get("session"), dict) else {}
    raw = section.get("historyLimitTurns") or section.get("history_limit_turns")
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.strip().isdigit():
        v = int(raw.strip())
        return v if v > 0 else None
    return None

