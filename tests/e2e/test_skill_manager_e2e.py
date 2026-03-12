"""End-to-end pytest tests for SkillManager with encryption support."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from mw4agent.skills import SkillManager, get_default_skill_manager
from mw4agent.crypto import EncryptionConfigError


@pytest.fixture
def temp_skills_dir(tmp_path: Path) -> Path:
    """Create a temporary skills directory for testing."""
    return tmp_path / "skills"


@pytest.fixture
def skill_manager(temp_skills_dir: Path) -> SkillManager:
    """Create a SkillManager with temporary directory."""
    return SkillManager(skills_dir=str(temp_skills_dir))


@pytest.fixture
def secret_key(monkeypatch) -> str:
    """Set up a test secret key."""
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv("MW4AGENT_SECRET_KEY", key)
    return key


def test_skill_manager_write_read_encrypted(skill_manager: SkillManager, secret_key: str) -> None:
    """Test writing and reading encrypted skill files."""
    skill_data = {
        "name": "File Operations",
        "description": "Read and write files",
        "tools": ["read_file", "write_file"],
        "examples": [
            "Read the file at /path/to/file.txt",
            "Write 'Hello' to /tmp/test.txt",
        ],
        "enabled": True,
    }

    # Write skill (should be encrypted)
    skill_manager.write_skill("file_operations", skill_data)

    # Verify file exists and is encrypted
    skill_path = skill_manager._get_skill_path("file_operations")
    assert skill_path.exists()
    raw_content = skill_path.read_bytes()
    assert raw_content.startswith(b"MW4AGENT_ENC_v1\n")

    # Read skill (should decrypt automatically)
    loaded_data = skill_manager.read_skill("file_operations")
    assert loaded_data == skill_data


def test_skill_manager_read_nonexistent(skill_manager: SkillManager) -> None:
    """Test reading a non-existent skill returns None."""
    result = skill_manager.read_skill("nonexistent")
    assert result is None


def test_skill_manager_list_skills(skill_manager: SkillManager, secret_key: str) -> None:
    """Test listing all skill files."""
    # Write multiple skills
    skill_manager.write_skill("skill1", {"name": "Skill 1"})
    skill_manager.write_skill("skill2", {"name": "Skill 2"})
    skill_manager.write_skill("skill3", {"name": "Skill 3"})

    # List skills
    skills = skill_manager.list_skills()
    assert len(skills) == 3
    assert "skill1" in skills
    assert "skill2" in skills
    assert "skill3" in skills


def test_skill_manager_read_all_skills(skill_manager: SkillManager, secret_key: str) -> None:
    """Test reading all skills at once."""
    skill_manager.write_skill("skill1", {"name": "Skill 1", "value": 1})
    skill_manager.write_skill("skill2", {"name": "Skill 2", "value": 2})

    all_skills = skill_manager.read_all_skills()
    assert len(all_skills) == 2
    assert all_skills["skill1"]["name"] == "Skill 1"
    assert all_skills["skill2"]["name"] == "Skill 2"


def test_skill_manager_delete_skill(skill_manager: SkillManager, secret_key: str) -> None:
    """Test deleting a skill file."""
    skill_manager.write_skill("to_delete", {"name": "To Delete"})
    assert skill_manager._get_skill_path("to_delete").exists()

    # Delete skill
    deleted = skill_manager.delete_skill("to_delete")
    assert deleted is True
    assert not skill_manager._get_skill_path("to_delete").exists()

    # Delete non-existent skill
    deleted = skill_manager.delete_skill("nonexistent")
    assert deleted is False


def test_skill_manager_plaintext_fallback(skill_manager: SkillManager, monkeypatch) -> None:
    """Test fallback to plaintext when encryption is not configured."""
    # Remove encryption key to force plaintext fallback
    # Also need to clear the cached store
    import mw4agent.crypto.secure_io
    monkeypatch.delenv("MW4AGENT_SECRET_KEY", raising=False)
    monkeypatch.setattr(mw4agent.crypto.secure_io, "_default_store", None)
    
    skill_data = {"name": "Plaintext Skill"}

    # Write without encryption (should fallback to plaintext)
    skill_manager.write_skill("plaintext_skill", skill_data)

    # Verify file is plaintext
    skill_path = skill_manager._get_skill_path("plaintext_skill")
    assert skill_path.exists()
    raw_content = skill_path.read_bytes()
    assert not raw_content.startswith(b"MW4AGENT_ENC_v1\n"), f"File should be plaintext but starts with magic header: {raw_content[:50]}"

    # Read should work (fallback enabled by default)
    loaded_data = skill_manager.read_skill("plaintext_skill")
    assert loaded_data == skill_data


def test_get_default_skill_manager(secret_key: str) -> None:
    """Test get_default_skill_manager returns a singleton."""
    mgr1 = get_default_skill_manager()
    mgr2 = get_default_skill_manager()
    assert mgr1 is mgr2
    assert mgr1.skills_dir == mgr2.skills_dir


def test_skill_manager_custom_directory(tmp_path: Path, secret_key: str) -> None:
    """Test SkillManager with custom directory."""
    custom_dir = tmp_path / "custom_skills"
    mgr = SkillManager(skills_dir=str(custom_dir))

    skill_data = {"name": "Custom Skill"}
    mgr.write_skill("custom", skill_data)

    assert (custom_dir / "custom.json").exists()
    loaded = mgr.read_skill("custom")
    assert loaded == skill_data
