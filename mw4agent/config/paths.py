"""Paths for workspace and state (OpenClaw-style: ~/.mw4agent/workspace)."""

from __future__ import annotations

import os
from pathlib import Path


def get_default_workspace_dir() -> str:
    """Return default agent workspace directory: ~/.mw4agent/workspace.

    Override with MW4AGENT_WORKSPACE_DIR env. Aligns with OpenClaw's
    ~/.openclaw/workspace.
    """
    env = os.environ.get("MW4AGENT_WORKSPACE_DIR")
    if env and env.strip():
        return os.path.abspath(env.strip())
    return str(Path.home() / ".mw4agent" / "workspace")


def ensure_workspace_dir() -> str:
    """Ensure default workspace dir exists; return its path."""
    path = get_default_workspace_dir()
    os.makedirs(path, exist_ok=True)
    return path
