"""Colored `[agent:id]` prefixes for multi-agent LLM trace logs (INFO).

Respects ``NO_COLOR`` and ``MW4AGENT_LOG_AGENT_COLORS`` (0|false|off disables).
Colors apply when stderr is a TTY (typical console dev).
"""

from __future__ import annotations

import hashlib
import os
import sys
from typing import Optional

_RESET = "\033[0m"
# Distinct bright ANSI colors (foreground)
_AGENT_COLORS = (
    "\033[1;31m",  # bright red
    "\033[1;32m",  # bright green
    "\033[1;33m",  # bright yellow
    "\033[1;34m",  # bright blue
    "\033[1;35m",  # bright magenta
    "\033[1;36m",  # bright cyan
    "\033[1;91m",  # bright red alt
    "\033[1;92m",  # bright green alt
    "\033[1;93m",  # bright yellow alt
    "\033[1;94m",  # bright blue alt
    "\033[1;95m",  # bright magenta alt
    "\033[1;96m",  # bright cyan alt
)


def colors_enabled() -> bool:
    if os.environ.get("NO_COLOR", "").strip():
        return False
    raw = os.environ.get("MW4AGENT_LOG_AGENT_COLORS", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


def _stable_color_for_label(label: str) -> str:
    h = hashlib.md5(label.encode("utf-8")).hexdigest()
    idx = int(h[:8], 16) % len(_AGENT_COLORS)
    return _AGENT_COLORS[idx]


def format_agent_tag(agent_id: Optional[str]) -> str:
    """Return plain or colored ``[agent:<id>]`` for log messages."""
    label = (str(agent_id).strip() if agent_id is not None else "") or "?"
    plain = f"[agent:{label}]"
    if not colors_enabled():
        return plain
    return f"{_stable_color_for_label(label)}{plain}{_RESET}"


def preview_one_line(text: str, *, max_len: int = 180) -> str:
    """Short single-line preview for logs (avoid huge records)."""
    s = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
