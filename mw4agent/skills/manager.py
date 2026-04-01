"""Skills manager with encrypted file storage.

This module provides a unified interface for reading/writing MW4Agent skill files
with automatic encryption support. Supports both JSON and Markdown (SKILL.md)
formats for OpenClaw compatibility.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..crypto import EncryptionConfigError, get_default_encrypted_store, is_encryption_enabled

from .format_md import parse_skill_markdown

# OpenClaw-style: <skillName>/SKILL.md
SKILL_MD_FILENAME = "SKILL.md"


class SkillManager:
    """Manages MW4Agent skill files with encryption support.

    Skill files are stored in `~/.mw4agent/skills/` by default.
    Supports:
      - JSON: ``<name>.json`` (encrypted or plaintext).
      - Markdown: ``<name>.md`` or ``<name>/SKILL.md`` (OpenClaw-compatible, plaintext).
    """

    def __init__(self, skills_dir: Optional[str] = None) -> None:
        """Initialize skill manager.

        Args:
            skills_dir: Base directory for skill files. Defaults to `~/.mw4agent/skills/`.
        """
        if skills_dir:
            self.skills_dir = Path(skills_dir)
        else:
            home = Path.home()
            self.skills_dir = home / ".mw4agent" / "skills"
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
        """
        base = self._normalize_name(name)
        candidates: List[Tuple[Path, str]] = [
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
        """List all skill names (JSON, .md, and <name>/SKILL.md; deduplicated by name)."""
        if not self.skills_dir.exists():
            return []
        seen: set = set()
        for path in self.skills_dir.glob("*.json"):
            seen.add(path.stem)
        for path in self.skills_dir.glob("*.md"):
            seen.add(path.stem)
        for path in self.skills_dir.iterdir():
            if path.is_dir():
                if (path / SKILL_MD_FILENAME).exists():
                    seen.add(path.name)
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
