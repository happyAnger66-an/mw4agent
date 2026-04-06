"""Root config: single file ~/.orbit/orbit.json for llm, skills, channels, etc."""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .manager import ConfigManager

# 默认全局配置文件路径，所有配置项（llm、skills、channels 等）均存于此文件
ROOT_CONFIG_FILENAME = "orbit.json"
LEGACY_ROOT_CONFIG_FILENAME = "mw4agent.json"

# Dedupe UserWarning when get_root_config_dir() is called repeatedly in one process.
_warned_multi_file_sets: set[frozenset[str]] = set()


def _root_config_stem(config_dir: Path) -> str:
    """Config file stem (without .json) to use under config_dir (orbit vs legacy mw4agent)."""
    if (config_dir / ROOT_CONFIG_FILENAME).exists():
        return ROOT_CONFIG_FILENAME.replace(".json", "")
    if (config_dir / LEGACY_ROOT_CONFIG_FILENAME).exists():
        return LEGACY_ROOT_CONFIG_FILENAME.replace(".json", "")
    return ROOT_CONFIG_FILENAME.replace(".json", "")


def _root_config_dir_candidates(home: Path) -> List[Tuple[Path, Path]]:
    """Ordered (config_dir, config_file) pairs for resolving the root config file."""
    orbit_dir = home / "orbit"
    dot_orbit = home / ".orbit"
    nested_orbit = orbit_dir / "config"
    nested_dot = dot_orbit / "config"
    old_home = home / ".mw4agent"
    old_nested = old_home / "config"
    return [
        (dot_orbit, dot_orbit / ROOT_CONFIG_FILENAME),
        (nested_dot, nested_dot / ROOT_CONFIG_FILENAME),
        (orbit_dir, orbit_dir / ROOT_CONFIG_FILENAME),
        (nested_orbit, nested_orbit / ROOT_CONFIG_FILENAME),
        (old_home, old_home / LEGACY_ROOT_CONFIG_FILENAME),
        (old_nested, old_nested / LEGACY_ROOT_CONFIG_FILENAME),
    ]


def list_existing_root_config_files(home: Optional[Path] = None) -> List[Path]:
    """Return all resolved paths that exist on disk (for debugging migration issues)."""
    h = home or Path.home()
    out: List[Path] = []
    for _, file_path in _root_config_dir_candidates(h):
        if file_path.exists():
            out.append(file_path.resolve())
    return out


def get_root_config_dir() -> Path:
    """Return the directory containing the root config file.

    Uses ORBIT_CONFIG_DIR or legacy MW4AGENT_CONFIG_DIR if set (e.g. for tests),
    otherwise prefers ``~/.orbit/orbit.json``, then ``~/.orbit/config/``, then
    non-hidden ``~/orbit/...`` (brief legacy layout), then ``~/.mw4agent/...``.
    Default directory for new installs is ``~/.orbit``.

    If **multiple** candidate files exist (e.g. both ``~/.orbit/orbit.json`` and
    ``~/orbit/orbit.json``), only the **first in this order** is used; others are
    ignored until removed or ``ORBIT_CONFIG_DIR`` is set. A warning is emitted once
    when this happens — a common cause of "config reset" is an empty
    ``~/.orbit/orbit.json`` shadowing a fuller ``~/orbit/orbit.json``.
    """
    env_dir = os.environ.get("ORBIT_CONFIG_DIR") or os.environ.get("MW4AGENT_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    home = Path.home()
    dot_orbit = home / ".orbit"
    checks = _root_config_dir_candidates(home)
    chosen: Optional[Tuple[Path, Path]] = None
    for dir_path, file_path in checks:
        if file_path.exists():
            chosen = (dir_path, file_path)
            break
    if chosen is None:
        return dot_orbit

    dir_path, primary = chosen
    all_existing = [fp for _, fp in checks if fp.exists()]
    if len(all_existing) > 1:
        key = frozenset(str(p.resolve()) for p in all_existing)
        if key not in _warned_multi_file_sets:
            _warned_multi_file_sets.add(key)
            other = [p.resolve() for p in all_existing if p.resolve() != primary.resolve()]
            if other:
                warnings.warn(
                    "Multiple Orbit root config files exist; loading only "
                    f"{primary.resolve()}. Ignored (merge or delete, or set ORBIT_CONFIG_DIR): "
                    + ", ".join(str(p) for p in other),
                    UserWarning,
                    stacklevel=2,
                )
    return dir_path


def _get_root_config_manager() -> ConfigManager:
    """Config manager that targets the root config directory.

    The single file used is orbit.json (or legacy mw4agent.json); all sections live inside.
    """
    return ConfigManager(config_dir=str(get_root_config_dir()))


def get_root_config_path() -> Path:
    """Return the absolute path to the root config file."""
    d = get_root_config_dir()
    return d / f"{_root_config_stem(d)}.json"


def read_root_config() -> Dict[str, Any]:
    """Read the full root config."""
    d = get_root_config_dir()
    stem = _root_config_stem(d)
    mgr = _get_root_config_manager()
    return mgr.read_config(stem, default={})


def write_root_config(data: Dict[str, Any]) -> None:
    """Write the full root config."""
    d = get_root_config_dir()
    stem = _root_config_stem(d)
    mgr = _get_root_config_manager()
    mgr.write_config(stem, data)


def read_root_section(section: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read one section (e.g. llm, skills, channels) from the root config file."""
    root = read_root_config()
    val = root.get(section)
    if isinstance(val, dict):
        return val
    return default if default is not None else {}


def write_root_section(section: str, data: Dict[str, Any]) -> None:
    """Write one section into the root config file (merge, then write)."""
    root = read_root_config()
    root[section] = data
    write_root_config(root)


class RootConfigManager(ConfigManager):
    """Config manager that reads/writes sections of the single root config file.

    read_config("llm") returns the "llm" key from ~/.orbit/orbit.json (or legacy path).
    write_config("llm", {...}) merges into that file.
    """

    def __init__(self) -> None:
        super().__init__(config_dir=str(get_root_config_dir()))

    def read_config(self, name: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Read a section by name from the root config file."""
        return read_root_section(name, default=default or {})

    def write_config(self, name: str, data: Dict[str, Any]) -> None:
        """Write a section by name into the root config file."""
        write_root_section(name, data)

    def delete_config(self, name: str) -> bool:
        """Remove a section from the root config file."""
        root = read_root_config()
        if name not in root:
            return False
        del root[name]
        write_root_config(root)
        return True

    def list_configs(self) -> list[str]:
        """List section names (top-level keys) in the root config file."""
        root = read_root_config()
        return sorted(k for k in root if isinstance(root[k], dict))
