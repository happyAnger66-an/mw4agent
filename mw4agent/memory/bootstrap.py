"""Load workspace bootstrap files into a single system-prompt string (OpenClaw-style)."""

from __future__ import annotations

import os
from typing import List, Tuple

from .search import BOOTSTRAP_ORDER_FOR_PROMPT


# 与 OpenClaw token-use 对齐：单文件/总字符上限
DEFAULT_MAX_CHARS_PER_FILE = 20_000
DEFAULT_TOTAL_MAX_CHARS = 150_000


def load_bootstrap_system_prompt(
    workspace_dir: str,
    *,
    max_chars_per_file: int = DEFAULT_MAX_CHARS_PER_FILE,
    total_max_chars: int = DEFAULT_TOTAL_MAX_CHARS,
    file_order: Tuple[str, ...] = BOOTSTRAP_ORDER_FOR_PROMPT,
) -> str:
    """Read bootstrap markdown files from workspace and concatenate with caps.

    Default file_order puts IDENTITY.md, USER.md, MEMORY.md first so the model
    reliably sees who the user is and what is in long-term memory (e.g. "我是谁").
    Each file is truncated to max_chars_per_file, and the total to total_max_chars.
    Returns a single string suitable for prepending to the LLM system prompt.
    """
    workspace_dir = os.path.normpath(os.path.abspath(workspace_dir))
    parts: List[str] = []
    total = 0
    for name in file_order:
        if total >= total_max_chars:
            break
        path = os.path.join(workspace_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError:
            continue
        cap = min(max_chars_per_file, total_max_chars - total)
        if len(raw) > cap:
            raw = raw[:cap] + "\n\n[... truncated ...]"
        if not raw.strip():
            continue
        parts.append(f"<!-- from {name} -->\n{raw}")
        total += len(parts[-1])
    return "\n\n".join(parts) if parts else ""
