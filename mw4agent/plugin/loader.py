"""Plugin discovery and loading: plugin.json parsing and tools_module registration."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mw4agent.log import get_logger

logger = get_logger(__name__)

MANIFEST_FILENAME = "plugin.json"
ENV_PLUGIN_DIR = "MW4AGENT_PLUGIN_DIR"
ENV_PLUGIN_ROOT = "MW4AGENT_PLUGIN_ROOT"
SKILL_MD_FILENAME = "SKILL.md"


@dataclass
class PluginInfo:
    """Resolved plugin: root path and parsed manifest."""

    root: Path
    manifest: Dict[str, Any]
    name: str

    @property
    def tools_module(self) -> Optional[str]:
        return self.manifest.get("tools_module")

    @property
    def skills_dir(self) -> Optional[str]:
        return self.manifest.get("skills_dir")


class PluginSkillSource:
    """Aggregates skill directories from plugins; reads skills with same convention as SkillManager (no encryption)."""

    def __init__(self) -> None:
        self._dirs: List[Path] = []

    def add_dir(self, path: Path) -> None:
        """Add a plugin skills directory (e.g. plugin_root / skills_dir)."""
        p = path.resolve()
        if p.is_dir() and p not in self._dirs:
            self._dirs.append(p)

    def _list_skills_in_dir(self, directory: Path) -> List[str]:
        """List skill names in one directory: *.json, *.md, <name>/SKILL.md."""
        if not directory.exists():
            return []
        seen: set = set()
        for f in directory.glob("*.json"):
            seen.add(f.stem)
        for f in directory.glob("*.md"):
            seen.add(f.stem)
        for f in directory.iterdir():
            if f.is_dir() and (f / SKILL_MD_FILENAME).exists():
                seen.add(f.name)
        return sorted(seen)

    def _read_skill_from_dir(self, directory: Path, name: str) -> Optional[Dict[str, Any]]:
        """Read one skill from a directory (plaintext JSON or Markdown)."""
        base = name
        for ext in (".json", ".md"):
            if base.endswith(ext):
                base = base[: -len(ext)]
                break
        candidates: List[Tuple[Path, str]] = [
            (directory / f"{base}.json", "json"),
            (directory / f"{base}.md", "md"),
            (directory / base / SKILL_MD_FILENAME, "md"),
        ]
        for path, fmt in candidates:
            if not path.exists():
                continue
            try:
                if fmt == "json":
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return data if isinstance(data, dict) else None
                else:
                    from mw4agent.skills.format_md import parse_skill_markdown
                    text = path.read_text(encoding="utf-8")
                    data = parse_skill_markdown(text)
                    if not data.get("name"):
                        data["name"] = base
                    return data
            except Exception as e:
                logger.debug("Failed to read skill %s from %s: %s", name, path, e)
        return None

    def read_all_skills(self) -> Dict[str, Dict[str, Any]]:
        """Read all skills from all registered plugin dirs; first occurrence of a name wins."""
        result: Dict[str, Dict[str, Any]] = {}
        for directory in self._dirs:
            for name in self._list_skills_in_dir(directory):
                if name in result:
                    continue
                skill_data = self._read_skill_from_dir(directory, name)
                if skill_data:
                    result[name] = skill_data
        return result


_plugin_skill_source: Optional[PluginSkillSource] = None


def get_plugin_skill_source() -> PluginSkillSource:
    """Singleton plugin skill source (used by load_plugins and build_skill_snapshot)."""
    global _plugin_skill_source
    if _plugin_skill_source is None:
        _plugin_skill_source = PluginSkillSource()
    return _plugin_skill_source


def _parse_plugin_dirs_from_env() -> List[Path]:
    """Return list of plugin directory paths from MW4AGENT_PLUGIN_DIR (colon or comma separated)."""
    raw = os.environ.get(ENV_PLUGIN_DIR, "").strip()
    if not raw:
        return []
    dirs: List[Path] = []
    for part in raw.replace(",", ":").split(":"):
        part = part.strip()
        if part:
            p = Path(part).expanduser().resolve()
            if p.exists() and p.is_dir():
                dirs.append(p)
            else:
                logger.warning("Plugin dir does not exist or is not a directory: %s", p)
    return dirs


def _get_plugin_dirs_from_config() -> List[Path]:
    """Return plugin_dirs from root config (plugins.plugin_dirs). Fallback when env is not set."""
    try:
        from mw4agent.config.root import read_root_section
        section = read_root_section("plugins", default={})
        if not isinstance(section, dict):
            return []
        raw = section.get("plugin_dirs")
        if isinstance(raw, list):
            dirs = []
            for part in raw:
                if isinstance(part, str) and part.strip():
                    p = Path(part.strip()).expanduser().resolve()
                    if p.exists() and p.is_dir():
                        dirs.append(p)
                    else:
                        logger.warning("Config plugin_dirs entry not a directory: %s", p)
            return dirs
        return []
    except Exception as e:
        logger.debug("Could not read plugins config: %s", e)
        return []


def _get_plugins_enabled_from_config() -> Optional[List[str]]:
    """Return plugins_enabled from root config (plugins.plugins_enabled). None means allow all."""
    try:
        from mw4agent.config.root import read_root_section
        section = read_root_section("plugins", default={})
        if not isinstance(section, dict):
            return None
        raw = section.get("plugins_enabled")
        if raw is None:
            return None
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return None
    except Exception as e:
        logger.debug("Could not read plugins config: %s", e)
        return None


def _collect_plugin_roots_from_dir(directory: Path) -> List[Path]:
    """If directory has plugin.json, return [directory]; else return subdirs that have plugin.json."""
    roots: List[Path] = []
    manifest_path = directory / MANIFEST_FILENAME
    if manifest_path.is_file():
        roots.append(directory)
        return roots
    try:
        for child in directory.iterdir():
            if child.is_dir() and (child / MANIFEST_FILENAME).is_file():
                roots.append(child)
    except OSError as e:
        logger.warning("Cannot list plugin directory %s: %s", directory, e)
    return roots


def discover_plugins(plugin_dirs: Optional[List[Path]] = None) -> List[PluginInfo]:
    """Discover plugins: scan directories for plugin.json and return PluginInfo list.

    If plugin_dirs is None: use MW4AGENT_PLUGIN_DIR env, then plugins.plugin_dirs from
    root config (~/.mw4agent/mw4agent.json) if env is empty. Each path can be either
    a plugin root (contains plugin.json) or a parent of multiple plugin roots.
    """
    if plugin_dirs is None:
        plugin_dirs = _parse_plugin_dirs_from_env()
        if not plugin_dirs:
            plugin_dirs = _get_plugin_dirs_from_config()
    if not plugin_dirs:
        return []

    seen_roots: set = set()
    infos: List[PluginInfo] = []

    for base in plugin_dirs:
        for root in _collect_plugin_roots_from_dir(base):
            if root in seen_roots:
                continue
            seen_roots.add(root)
            manifest_path = root / MANIFEST_FILENAME
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Failed to read plugin manifest %s: %s", manifest_path, e)
                continue
            if not isinstance(manifest, dict):
                logger.warning("Plugin manifest is not a dict: %s", manifest_path)
                continue
            name = (manifest.get("name") or "").strip()
            if not name:
                logger.warning("Plugin manifest missing 'name': %s", manifest_path)
                continue
            infos.append(PluginInfo(root=root, manifest=manifest, name=name))

    return infos


def _load_plugin_tools(plugin: PluginInfo, registry: Any) -> None:
    """Load plugin's tools_module and call register_tools(registry) or register(registry)."""
    tools_module_name = plugin.tools_module
    if not tools_module_name or not isinstance(tools_module_name, str):
        return
    tools_module_name = tools_module_name.strip()
    if not tools_module_name:
        return

    root = plugin.root
    # Prefer tools.py, then tools/__init__.py
    module_file = root / f"{tools_module_name}.py"
    if not module_file.is_file():
        module_file = root / tools_module_name / "__init__.py"
    if not module_file.is_file():
        logger.warning("Plugin %s tools_module '%s' not found at %s", plugin.name, tools_module_name, root)
        return

    spec = importlib.util.spec_from_file_location(
        f"mw4agent_plugin_{plugin.name}_{tools_module_name}",
        module_file,
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        logger.warning("Cannot create spec for plugin module %s", module_file)
        return

    module = importlib.util.module_from_spec(spec)
    old_plugin_root = os.environ.get(ENV_PLUGIN_ROOT)
    try:
        os.environ[ENV_PLUGIN_ROOT] = str(root)
        spec.loader.exec_module(module)
    finally:
        if old_plugin_root is not None:
            os.environ[ENV_PLUGIN_ROOT] = old_plugin_root
        elif ENV_PLUGIN_ROOT in os.environ:
            os.environ.pop(ENV_PLUGIN_ROOT, None)

    if hasattr(module, "register_tools"):
        module.register_tools(registry)
        logger.info("Plugin %s registered tools via register_tools()", plugin.name)
    elif hasattr(module, "register"):
        module.register(registry)
        logger.info("Plugin %s registered tools via register()", plugin.name)
    else:
        logger.warning("Plugin %s tools_module has no register_tools or register callable", plugin.name)


def load_plugins(
    plugin_dirs: Optional[List[Path]] = None,
    registry: Optional[Any] = None,
    register_tools_only: bool = True,
) -> List[PluginInfo]:
    """Discover plugins and load their tools into the tool registry.

    Args:
        plugin_dirs: Directories to scan; if None, use MW4AGENT_PLUGIN_DIR.
        registry: Tool registry to register tools into; if None, use get_tool_registry().
        register_tools_only: If True (default), only load tools_module; skills_dir/hooks left for phase 2.

    Returns:
        List of successfully discovered PluginInfo (tools are registered for those with tools_module).
    """
    if registry is None:
        from mw4agent.agents.tools import get_tool_registry
        registry = get_tool_registry()

    infos = discover_plugins(plugin_dirs)
    if not infos:
        return []

    plugins_enabled = _get_plugins_enabled_from_config()
    if plugins_enabled is not None:
        allowed = set(plugins_enabled)
        infos = [p for p in infos if p.name in allowed]
        logger.debug("Plugins filtered by plugins_enabled: %s", list(allowed))

    plugin_skill_source = get_plugin_skill_source()
    loaded: List[PluginInfo] = []
    for plugin in infos:
        if plugin.tools_module:
            try:
                _load_plugin_tools(plugin, registry)
                loaded.append(plugin)
            except ValueError as e:
                logger.error("Plugin %s tool registration failed (duplicate name?): %s", plugin.name, e)
                raise
            except Exception as e:
                logger.exception("Plugin %s tools_module load failed: %s", plugin.name, e)
                raise
        else:
            loaded.append(plugin)

        if plugin.skills_dir and isinstance(plugin.skills_dir, str):
            skills_path = (plugin.root / plugin.skills_dir.strip()).resolve()
            plugin_skill_source.add_dir(skills_path)
            logger.info("Plugin %s added skills_dir %s", plugin.name, skills_path)

    return loaded
