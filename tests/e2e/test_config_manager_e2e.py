"""End-to-end pytest tests for ConfigManager with encryption support."""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

import pytest

from mw4agent.config import ConfigManager, get_default_config_manager
from mw4agent.crypto import EncryptionConfigError, get_default_encrypted_store


@pytest.fixture
def temp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory for testing."""
    return tmp_path / "config"


@pytest.fixture
def config_manager(temp_config_dir: Path) -> ConfigManager:
    """Create a ConfigManager with temporary directory."""
    return ConfigManager(config_dir=str(temp_config_dir))


@pytest.fixture
def secret_key(monkeypatch) -> str:
    """Set up a test secret key."""
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv("MW4AGENT_SECRET_KEY", key)
    return key


def test_config_manager_write_read_encrypted(config_manager: ConfigManager, secret_key: str) -> None:
    """Test writing and reading encrypted config files."""
    config_data = {
        "gateway": {
            "port": 18789,
            "bind": "127.0.0.1",
        },
        "agent": {
            "default_model": "gpt-4o-mini",
            "timeout_seconds": 30,
        },
    }

    # Write config (should be encrypted)
    config_manager.write_config("test_config", config_data)

    # Verify file exists and is encrypted
    config_path = config_manager._get_config_path("test_config")
    assert config_path.exists()
    raw_content = config_path.read_bytes()
    assert raw_content.startswith(b"MW4AGENT_ENC_v1\n")

    # Read config (should decrypt automatically)
    loaded_data = config_manager.read_config("test_config")
    assert loaded_data == config_data


def test_config_manager_read_nonexistent(config_manager: ConfigManager) -> None:
    """Test reading a non-existent config returns default."""
    default = {"key": "value"}
    result = config_manager.read_config("nonexistent", default=default)
    assert result == default


def test_config_manager_list_configs(config_manager: ConfigManager, secret_key: str) -> None:
    """Test listing all config files."""
    # Write multiple configs
    config_manager.write_config("config1", {"key1": "value1"})
    config_manager.write_config("config2", {"key2": "value2"})
    config_manager.write_config("config3", {"key3": "value3"})

    # List configs
    configs = config_manager.list_configs()
    assert len(configs) == 3
    assert "config1" in configs
    assert "config2" in configs
    assert "config3" in configs


def test_config_manager_delete_config(config_manager: ConfigManager, secret_key: str) -> None:
    """Test deleting a config file."""
    config_manager.write_config("to_delete", {"key": "value"})
    assert config_manager._get_config_path("to_delete").exists()

    # Delete config
    deleted = config_manager.delete_config("to_delete")
    assert deleted is True
    assert not config_manager._get_config_path("to_delete").exists()

    # Delete non-existent config
    deleted = config_manager.delete_config("nonexistent")
    assert deleted is False


def test_config_manager_plaintext_fallback(config_manager: ConfigManager, monkeypatch) -> None:
    """Test fallback to plaintext when encryption is not configured."""
    # Remove encryption key to force plaintext fallback
    # Also need to clear the cached store
    from mw4agent.crypto.secure_io import _default_store
    import mw4agent.crypto.secure_io
    monkeypatch.delenv("MW4AGENT_SECRET_KEY", raising=False)
    monkeypatch.setattr(mw4agent.crypto.secure_io, "_default_store", None)
    
    config_data = {"key": "value"}

    # Write without encryption (should fallback to plaintext)
    config_manager.write_config("plaintext_config", config_data)

    # Verify file is plaintext
    config_path = config_manager._get_config_path("plaintext_config")
    assert config_path.exists()
    raw_content = config_path.read_bytes()
    assert not raw_content.startswith(b"MW4AGENT_ENC_v1\n"), f"File should be plaintext but starts with magic header: {raw_content[:50]}"

    # Read should work (fallback enabled by default)
    loaded_data = config_manager.read_config("plaintext_config")
    assert loaded_data == config_data


def test_get_default_config_manager(secret_key: str) -> None:
    """Test get_default_config_manager returns a singleton."""
    mgr1 = get_default_config_manager()
    mgr2 = get_default_config_manager()
    assert mgr1 is mgr2
    assert mgr1.config_dir == mgr2.config_dir


def test_config_manager_custom_directory(tmp_path: Path, secret_key: str) -> None:
    """Test ConfigManager with custom directory."""
    custom_dir = tmp_path / "custom_config"
    mgr = ConfigManager(config_dir=str(custom_dir))

    config_data = {"test": "data"}
    mgr.write_config("custom", config_data)

    assert (custom_dir / "custom.json").exists()
    loaded = mgr.read_config("custom")
    assert loaded == config_data
