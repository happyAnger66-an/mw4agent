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
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
        # v2: add leaf pointer records (type=leaf) and message id/parentId chain.
        "version": 2,
        "id": validate_session_id(session_id),
        "timestamp": _iso_now(),
        "cwd": cwd,
    }
    path.write_text(json.dumps(header, ensure_ascii=False) + "\n", encoding="utf-8")


def _gen_entry_id() -> str:
    # Good enough for local transcripts (monotonic-ish + random bits).
    return f"e{_now_ms()}-{os.urandom(4).hex()}"


def _scan_leaf_id(*, transcript_file: str) -> Optional[str]:
    path = Path(transcript_file)
    if not path.exists():
        return None
    leaf: Optional[str] = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("type") == "leaf":
            v = obj.get("leafId")
            if isinstance(v, str) and v.strip():
                leaf = v.strip()
            else:
                leaf = None
    return leaf


def _append_leaf_pointer(*, f, leaf_id: Optional[str]) -> None:
    record = {"type": "leaf", "timestamp": _now_ms(), "leafId": leaf_id}
    f.write(json.dumps(record, ensure_ascii=False) + "\n")


def branch_to_parent(*, transcript_file: str, parent_id: Optional[str]) -> None:
    """Move leaf pointer to parent_id (OpenClaw-like branch/resetLeaf).

    This does NOT delete history; it only appends a leaf pointer record so that
    future context building can start from a safe leaf.
    """
    path = Path(transcript_file)
    if not path.exists():
        return
    with path.open("a", encoding="utf-8") as f:
        _append_leaf_pointer(f=f, leaf_id=parent_id)


def append_compaction(
    *,
    transcript_file: str,
    session_id: str,
    cwd: str,
    summary: str,
) -> str:
    """Append a compaction entry as a system message and move leaf to it.

    Returns the entry id.
    """
    ensure_session_header(transcript_file=transcript_file, session_id=session_id, cwd=cwd)
    path = Path(transcript_file)
    parent_id = _scan_leaf_id(transcript_file=transcript_file)
    eid = _gen_entry_id()
    msg = {"role": "system", "content": str(summary or "").strip()}
    record = {
        "type": "compaction",
        "timestamp": _now_ms(),
        "id": eid,
        "parentId": parent_id,
        "message": msg,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        _append_leaf_pointer(f=f, leaf_id=eid)
    return eid


def append_custom(
    *,
    transcript_file: str,
    session_id: str,
    cwd: str,
    data: Dict[str, Any],
) -> None:
    """Append a custom entry (not injected as chat message)."""
    ensure_session_header(transcript_file=transcript_file, session_id=session_id, cwd=cwd)
    path = Path(transcript_file)
    record = {"type": "custom", "timestamp": _now_ms(), "data": data}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_messages(
    *,
    transcript_file: str,
    session_id: str,
    cwd: str,
    messages: Iterable[Dict[str, Any]],
) -> None:
    ensure_session_header(transcript_file=transcript_file, session_id=session_id, cwd=cwd)
    path = Path(transcript_file)
    parent_id = _scan_leaf_id(transcript_file=transcript_file)
    with path.open("a", encoding="utf-8") as f:
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").strip()
            if not role:
                continue
            eid = _gen_entry_id()
            record = {
                "type": "message",
                "timestamp": _now_ms(),
                "id": eid,
                "parentId": parent_id,
                "message": msg,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            parent_id = eid
        _append_leaf_pointer(f=f, leaf_id=parent_id)


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
        # Include any entry that has a message payload (message/compaction/etc).
        if isinstance(obj, dict) and isinstance(obj.get("message"), dict):
            out.append(obj["message"])
    if limit is not None and limit > 0 and len(out) > limit:
        return out[-limit:]
    return out


def build_messages_from_leaf(
    *,
    transcript_file: str,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Reconstruct messages by following the leaf/parentId chain.

    This is the OpenClaw/Pi-style behavior: if a later branch_to_parent() moved
    the leaf, we only include messages reachable from that leaf.
    """
    path = Path(transcript_file)
    if not path.exists():
        return []
    # Load all entries into maps.
    entries: Dict[str, Dict[str, Any]] = {}
    leaf_id: Optional[str] = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "leaf":
            v = obj.get("leafId")
            leaf_id = v.strip() if isinstance(v, str) and v.strip() else None
            continue
        eid = obj.get("id")
        if isinstance(eid, str) and eid.strip() and isinstance(obj.get("message"), dict):
            entries[eid] = obj

    # Walk back from leaf_id.
    chain: List[Dict[str, Any]] = []
    seen: set[str] = set()
    cur = leaf_id
    while cur:
        if cur in seen:
            break
        seen.add(cur)
        ent = entries.get(cur)
        if not ent:
            break
        msg = ent.get("message")
        if isinstance(msg, dict):
            chain.append(msg)
        pid = ent.get("parentId")
        cur = pid.strip() if isinstance(pid, str) and pid.strip() else None
    chain.reverse()
    if limit is not None and limit > 0 and len(chain) > limit:
        return chain[-limit:]
    return chain


def get_leaf_entry_meta(*, transcript_file: str) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Return (leaf_id, parent_id, leaf_message_dict) for the current leaf.

    If leaf points to a non-message entry or cannot be resolved, returns (leaf_id, None, None).
    """
    path = Path(transcript_file)
    if not path.exists():
        return (None, None, None)
    entries: Dict[str, Dict[str, Any]] = {}
    leaf_id: Optional[str] = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "leaf":
            v = obj.get("leafId")
            leaf_id = v.strip() if isinstance(v, str) and v.strip() else None
            continue
        eid = obj.get("id")
        if isinstance(eid, str) and eid.strip() and isinstance(obj.get("message"), dict):
            entries[eid] = obj
    if not leaf_id:
        return (None, None, None)
    ent = entries.get(leaf_id)
    if not ent:
        return (leaf_id, None, None)
    pid = ent.get("parentId")
    parent_id = pid.strip() if isinstance(pid, str) and pid.strip() else None
    msg = ent.get("message") if isinstance(ent.get("message"), dict) else None
    return (leaf_id, parent_id, msg)

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


def split_by_user_turns(
    messages: List[Dict[str, Any]],
    *,
    keep_last_user_turns: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split messages into (older, keep) where keep contains last N user turns (and following assistant/tool msgs)."""
    if keep_last_user_turns <= 0:
        return (messages, [])
    user_count = 0
    last_user_index = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_count += 1
            if user_count > keep_last_user_turns:
                return (messages[:last_user_index], messages[last_user_index:])
            last_user_index = i
    return ([], messages)


def format_compaction_summary(
    older_messages: List[Dict[str, Any]],
    *,
    max_chars: int = 4000,
) -> str:
    """Create a deterministic compaction summary (no extra LLM call)."""
    parts: List[str] = []
    for m in older_messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip() or "unknown"
        content = m.get("content")
        text = str(content) if content is not None else ""
        text = text.strip()
        if not text:
            continue
        parts.append(f"- [{role}] {text}")
        if sum(len(x) for x in parts) > max_chars:
            break
    body = "\n".join(parts)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n…"
    return "Session compaction summary (auto):\n" + body if body else "Session compaction summary (auto)."


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

