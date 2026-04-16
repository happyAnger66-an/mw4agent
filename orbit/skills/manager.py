"""Skills manager with encrypted file storage.

This module provides a unified interface for reading/writing Orbit skill files
with automatic encryption support. Supports both JSON and Markdown (SKILL.md)
formats for OpenClaw compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config.paths import get_state_dir
from ..crypto import EncryptionConfigError, get_default_encrypted_store, is_encryption_enabled

from .format_md import parse_skill_markdown

# OpenClaw-style: <skillName>/SKILL.md
SKILL_MD_FILENAME = "SKILL.md"


def _is_skill_bundle_parent_dir(dir_path: Path) -> bool:
    """True when ``dir_path`` is a *-skill bundle root (children hold SKILL.md).

    Layout: ``<skillsDir>/<name>-skill/<child>/SKILL.md``. If the parent directory
    itself contains ``SKILL.md``, it is treated as a normal single skill only.
    """
    return (
        dir_path.is_dir()
        and dir_path.name.endswith("-skill")
        and not (dir_path / SKILL_MD_FILENAME).exists()
    )


class SkillManager:
    """Manages Orbit skill files with encryption support.

    Skill files are stored in ``<get_state_dir()>/skills`` by default (typically ``~/.orbit/skills``).
    Supports:
      - JSON: ``<name>.json`` (encrypted or plaintext).
      - Markdown: ``<name>.md`` or ``<name>/SKILL.md`` (OpenClaw-compatible, plaintext).
      - Bundle: ``<name>-skill/<child>/SKILL.md`` or ``<name>-skill/<child>.json`` / ``.md`` under the parent (parent must not contain ``SKILL.md``); logical name ``<name>-skill/<child>``.
    """

    def __init__(self, skills_dir: Optional[str] = None) -> None:
        """Initialize skill manager.

        Args:
            skills_dir: Base directory for skill files. Defaults to ``<state_dir>/skills`` (see ``get_state_dir()``).
        """
        if skills_dir:
            self.skills_dir = Path(skills_dir)
        else:
            # Align with get_state_dir(): ~/.orbit/skills, ~/orbit/skills (legacy), etc.
            self.skills_dir = Path(get_state_dir()) / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_name(self, name: str) -> str:
        """Return base name without .json / .md extension."""
        for ext in (".json", ".md"):
            if name.endswith(ext):
                return name[: -len(ext)]
        return name

    def _resolve_skill_path(self, name: str) -> Optional[Tuple[Path, str]]:
        """Resolve skill name to (path, format). format is 'json' or 'md'.

        Tries in order: <name>.json, <name>.md, <name>/SKILL.md.
        For nested names ``parent/child`` (bundle layout): under ``parent/`` try
        ``child.json``, ``child.md``, ``child/SKILL.md``.
        """
        base = self._normalize_name(name)
        if "/" in base:
            parts = [p for p in base.split("/") if p and p not in (".", "..")]
            if len(parts) >= 2:
                parent_dir = self.skills_dir / parts[0]
                # Nested layout is only for ``*-skill`` bundle roots with no parent SKILL.md.
                if not _is_skill_bundle_parent_dir(parent_dir):
                    return None
                prefix = self.skills_dir.joinpath(*parts[:-1])
                leaf = parts[-1]
                candidates: List[Tuple[Path, str]] = [
                    (prefix / f"{leaf}.json", "json"),
                    (prefix / f"{leaf}.md", "md"),
                    (prefix / leaf / SKILL_MD_FILENAME, "md"),
                ]
                for path, fmt in candidates:
                    if path.exists():
                        return (path, fmt)
                return None

        candidates = [
            (self.skills_dir / f"{base}.json", "json"),
            (self.skills_dir / f"{base}.md", "md"),
            (self.skills_dir / base / SKILL_MD_FILENAME, "md"),
        ]
        for path, fmt in candidates:
            if path.exists():
                return (path, fmt)
        return None

    def _get_skill_path(self, name: str) -> Path:
        """Get path for a JSON skill file (used for write and delete)."""
        base = self._normalize_name(name)
        if "/" in base:
            parts = [p for p in base.split("/") if p and p not in (".", "..")]
            if len(parts) >= 2:
                return self.skills_dir.joinpath(*parts[:-1]) / f"{parts[-1]}.json"
        return self.skills_dir / f"{base}.json"

    def read_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Read a skill file (JSON or Markdown / SKILL.md).

        Args:
            name: Skill name with or without extension.

        Returns:
            Skill dictionary (name, description, ...), or None if not found.
        """
        resolved = self._resolve_skill_path(name)
        if not resolved:
            return None
        path, fmt = resolved

        if fmt == "json":
            if is_encryption_enabled():
                try:
                    store = get_default_encrypted_store()
                    data = store.read_json(str(path), fallback_plaintext=True)
                    if isinstance(data, dict):
                        return data
                    return None
                except EncryptionConfigError as e:
                    print(f"Warning: Encryption not configured, falling back to plaintext: {e}")
                except Exception as e:
                    print(f"Warning: Failed to load skill (encrypted path): {e}")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else None
            except Exception as e2:
                print(f"Warning: Failed to load plaintext skill: {e2}")
                return None
        else:
            try:
                text = path.read_text(encoding="utf-8")
                data = parse_skill_markdown(text)
                if not data.get("name") and name:
                    data["name"] = self._normalize_name(name)
                return data
            except Exception as e:
                print(f"Warning: Failed to load Markdown skill: {e}")
                return None

    def write_skill(self, name: str, data: Dict[str, Any]) -> None:
        """Write a skill file as JSON (encrypted when configured).

        Args:
            name: Skill file name (with or without .json extension).
            data: Skill dictionary to write.
        """
        path = self._get_skill_path(name)
        if is_encryption_enabled():
            try:
                store = get_default_encrypted_store()
                store.write_json(str(path), data)
                return
            except EncryptionConfigError as e:
                print(f"Warning: Encryption not configured, writing plaintext skill: {e}")
            except Exception as e:
                print(f"Warning: Failed to write skill (encrypted path): {e}")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def delete_skill(self, name: str) -> bool:
        """Delete a skill file (JSON, .md, or <name>/SKILL.md).

        Returns:
            True if the file was deleted, False if not found.
        """
        resolved = self._resolve_skill_path(name)
        if not resolved:
            return False
        path, _ = resolved
        if path.exists() and path.is_file():
            path.unlink()
            return True
        return False

    def list_skills(self) -> List[str]:
        """List all skill names (JSON, .md, and <name>/SKILL.md; deduplicated by name).

        Also supports bundle roots named ``*-skill``: when the parent has no
        ``SKILL.md``, each immediate subdirectory that contains ``SKILL.md`` is
        listed as ``<parent>/<child>``.
        """
        if not self.skills_dir.exists():
            return []
        seen: set = set()
        for path in self.skills_dir.glob("*.json"):
            seen.add(path.stem)
        for path in self.skills_dir.glob("*.md"):
            seen.add(path.stem)
        for path in self.skills_dir.iterdir():
            if not path.is_dir():
                continue
            if (path / SKILL_MD_FILENAME).exists():
                seen.add(path.name)
                continue
            if _is_skill_bundle_parent_dir(path):
                for sub in path.iterdir():
                    if sub.name.startswith("."):
                        continue
                    if sub.is_dir():
                        if (sub / SKILL_MD_FILENAME).exists():
                            seen.add(f"{path.name}/{sub.name}")
                    elif sub.is_file() and sub.suffix in (".json", ".md"):
                        seen.add(f"{path.name}/{sub.stem}")
        return sorted(seen)

    def read_all_skills(self) -> Dict[str, Dict[str, Any]]:
        """Read all skill files (JSON and Markdown).

        Returns:
            Dictionary mapping skill names to skill data.
        """
        result = {}
        for name in self.list_skills():
            skill_data = self.read_skill(name)
            if skill_data:
                result[name] = skill_data
        return result


_default_skill_manager: Optional[SkillManager] = None


def get_default_skill_manager() -> SkillManager:
    """Get the default skill manager instance."""
    global _default_skill_manager
    if _default_skill_manager is None:
        _default_skill_manager = SkillManager()
    return _default_skill_manager
