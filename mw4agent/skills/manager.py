"""Skills manager with encrypted file storage.

This module provides a unified interface for reading/writing MW4Agent skill files
with automatic encryption support.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..crypto import EncryptionConfigError, get_default_encrypted_store


class SkillManager:
    """Manages MW4Agent skill files with encryption support.

    Skill files are stored in `~/.mw4agent/skills/` by default.
    All files are automatically encrypted using the encryption framework.
    """

    def __init__(self, skills_dir: Optional[str] = None) -> None:
        """Initialize skill manager.

        Args:
            skills_dir: Base directory for skill files.

        Priority:
        1. Explicit skills_dir argument
        2. Environment variable MW4AGENT_SKILLS_DIR
        3. Default: ~/.mw4agent/skills
        """
        if skills_dir:
            self.skills_dir = Path(skills_dir)
        else:
            env_dir = os.getenv("MW4AGENT_SKILLS_DIR")
            if env_dir:
                self.skills_dir = Path(env_dir)
            else:
                home = Path.home()
                self.skills_dir = home / ".mw4agent" / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def _get_skill_path(self, name: str) -> Path:
        """Get full path for a skill file."""
        if not name.endswith(".json"):
            name = f"{name}.json"
        return self.skills_dir / name

    def read_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Read a skill file.

        Args:
            name: Skill file name (with or without .json extension).

        Returns:
            Skill dictionary, or None if file doesn't exist.
        """
        path = self._get_skill_path(name)
        if not path.exists():
            return None

        try:
            store = get_default_encrypted_store()
            data = store.read_json(str(path), fallback_plaintext=True)
            if isinstance(data, dict):
                return data
            return None
        except EncryptionConfigError as e:
            # If encryption is not configured, try plaintext fallback
            print(f"Warning: Encryption not configured, falling back to plaintext: {e}")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
                    return None
            except Exception as e2:
                print(f"Warning: Failed to load plaintext skill: {e2}")
                return None

    def write_skill(self, name: str, data: Dict[str, Any]) -> None:
        """Write a skill file (encrypted).

        Args:
            name: Skill file name (with or without .json extension).
            data: Skill dictionary to write.
        """
        path = self._get_skill_path(name)
        try:
            store = get_default_encrypted_store()
            store.write_json(str(path), data)
        except EncryptionConfigError:
            # Fallback to plaintext if encryption is not configured
            print("Warning: Encryption not configured, writing plaintext skill")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def delete_skill(self, name: str) -> bool:
        """Delete a skill file.

        Returns:
            True if file was deleted, False if it didn't exist.
        """
        path = self._get_skill_path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_skills(self) -> List[str]:
        """List all skill file names (without .json extension)."""
        if not self.skills_dir.exists():
            return []
        skills = []
        for path in self.skills_dir.glob("*.json"):
            skills.append(path.stem)
        return sorted(skills)

    def read_all_skills(self) -> Dict[str, Dict[str, Any]]:
        """Read all skill files.

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
