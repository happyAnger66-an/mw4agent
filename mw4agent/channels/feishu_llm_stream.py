"""Feishu formatting for AgentRunner ``llm`` stream events (thinking + tool plans)."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Optional, Tuple

from mw4agent.agents.types import StreamEvent
from mw4agent.config.root import read_root_section

if TYPE_CHECKING:
    from mw4agent.agents.session.manager import SessionManager

# SessionEntry.metadata: True/False set by /thinking and /close_thinking; absent → use global default.
FEISHU_LLM_STREAM_META_KEY = "feishu_llm_stream_push"

_RE_THINKING_ON = re.compile(r"^\s*/thinking(?:\s+|$)(.*)$", re.DOTALL)
_RE_THINKING_OFF = re.compile(r"^\s*/close_thinking(?:\s+|$)(.*)$", re.DOTALL)

_MAX_THINK = 3500
_MAX_CONTENT = 2500


def _truncate(s: str, max_chars: int) -> str:
    t = (s or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1] + "…"


def feishu_llm_stream_enabled() -> bool:
    """Whether to push ``stream=llm`` events to Feishu (direct AgentRunner only)."""
    env = os.environ.get("MW4AGENT_FEISHU_LLM_STREAM", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    try:
        ch = read_root_section("channels", default={})
        fe = ch.get("feishu") if isinstance(ch, dict) else None
        if isinstance(fe, dict) and fe.get("llm_stream_messages") is False:
            return False
    except Exception:
        pass
    return True


def parse_feishu_thinking_command(text: str) -> Tuple[Optional[bool], str]:
    """Parse leading ``/thinking`` or ``/close_thinking``.

    Returns:
        (True, remainder) — enable LLM stream push for this session
        (False, remainder) — disable
        (None, original_text) — no command at message start
    """
    raw = text or ""
    m = _RE_THINKING_ON.match(raw)
    if m:
        return True, (m.group(1) or "").strip()
    m = _RE_THINKING_OFF.match(raw)
    if m:
        return False, (m.group(1) or "").strip()
    return None, raw


def feishu_session_effective_llm_stream(
    session_manager: "SessionManager",
    session_id: str,
) -> bool:
    """Whether to subscribe/push ``stream=llm`` for this Feishu session (direct AgentRunner).

    Global :func:`feishu_llm_stream_enabled` must be true (env + root config). If the session
    has ``metadata[FEISHU_LLM_STREAM_META_KEY]``, it further gates: both must allow push.
    """
    base = feishu_llm_stream_enabled()
    entry = session_manager.get_session(session_id)
    if entry is None:
        return base
    meta = entry.metadata if isinstance(entry.metadata, dict) else {}
    if FEISHU_LLM_STREAM_META_KEY in meta:
        return base and bool(meta.get(FEISHU_LLM_STREAM_META_KEY))
    return base


def format_llm_stream_event_for_feishu(event: StreamEvent) -> Optional[str]:
    """Build a short Feishu markdown chunk from one ``llm`` / ``message`` event."""
    if event.stream != "llm" or event.type != "message":
        return None
    data = event.data if isinstance(event.data, dict) else {}
    phase = str(data.get("phase") or "")
    rnd = data.get("round")
    head = f"[模型] r{rnd}·{phase}" if rnd is not None else f"[模型] {phase}"

    chunks: list[str] = []
    thinking = data.get("thinking")
    if thinking and str(thinking).strip():
        chunks.append(f"{head}\n[思考]\n{_truncate(str(thinking), _MAX_THINK)}")

    content = data.get("content")
    if content and str(content).strip():
        chunks.append(f"[片段]\n{_truncate(str(content), _MAX_CONTENT)}")

    tcs = data.get("tool_calls")
    if isinstance(tcs, list) and tcs:
        names = []
        for tc in tcs:
            if isinstance(tc, dict) and tc.get("name"):
                names.append(str(tc["name"]))
        if names:
            chunks.append(f"[计划工具] " + ", ".join(names))

    if not chunks:
        return None
    return "\n".join(chunks)
