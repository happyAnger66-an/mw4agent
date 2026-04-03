"""Load workspace bootstrap files into a single system-prompt string (OpenClaw-style)."""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from .search import BOOTSTRAP_ORDER_FOR_PROMPT


# 与 OpenClaw token-use 对齐：单文件/总字符上限
DEFAULT_MAX_CHARS_PER_FILE = 20_000
DEFAULT_TOTAL_MAX_CHARS = 150_000

# Identity / docs only (orchestration runs read MEMORY from ``orch_workspace_dir``).
BOOTSTRAP_IDENTITY_FILES_FOR_ORCH = (
    "IDENTITY.md",
    "USER.md",
    "AGENTS.md",
    "SOUL.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
)
BOOTSTRAP_MEMORY_FILES_FOR_ORCH = ("MEMORY.md", "memory.md")


def load_bootstrap_for_orchestration(
    agent_workspace_dir: str,
    orch_agent_workspace_dir: str,
    *,
    max_chars_per_file: int = DEFAULT_MAX_CHARS_PER_FILE,
    total_max_chars: int = DEFAULT_TOTAL_MAX_CHARS,
) -> str:
    """Bootstrap prompt for gateway orchestration: identity from agent dir, memory from orch workspace."""
    identity = load_bootstrap_system_prompt(
        agent_workspace_dir,
        max_chars_per_file=max_chars_per_file,
        total_max_chars=total_max_chars,
        file_order=BOOTSTRAP_IDENTITY_FILES_FOR_ORCH,
    )
    memory = load_bootstrap_system_prompt(
        orch_agent_workspace_dir,
        max_chars_per_file=max_chars_per_file,
        total_max_chars=total_max_chars,
        file_order=BOOTSTRAP_MEMORY_FILES_FOR_ORCH,
    )
    parts = [p for p in (identity, memory) if p.strip()]
    return "\n\n".join(parts)


def load_orchestration_team_agents_appendix(
    orch_root_dir: str,
    *,
    max_chars_per_file: int = DEFAULT_MAX_CHARS_PER_FILE,
) -> str:
    """Team-level ``AGENTS.md`` at orchestration root, appended last to ``extra_system_prompt``.

    File: ``<orchestrations>/<orchId>/AGENTS.md`` (or ``agents.md`` if that is the only existing file).
    """
    root = os.path.normpath(os.path.abspath(orch_root_dir))
    candidates = ("AGENTS.md", "agents.md")
    chosen: Optional[str] = None
    for name in candidates:
        path = os.path.join(root, name)
        if os.path.isfile(path):
            chosen = name
            break
    if not chosen:
        return ""
    path = os.path.join(root, chosen)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return ""
    if len(raw) > max_chars_per_file:
        raw = raw[:max_chars_per_file] + "\n\n[... truncated ...]"
    if not raw.strip():
        return ""
    return (
        "<!-- orchestration AGENTS.md (team workflow / constraints; appended after orchestration hint) -->\n"
        f"{raw.strip()}"
    )


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
