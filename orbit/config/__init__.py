"""Configuration management with encryption support.

By default all config (llm, skills, channels, etc.) is stored in a single file:
  ~/.orbit/orbit.json

get_default_config_manager() returns a manager that reads/writes sections of that file.
"""

from .manager import ConfigManager, get_default_config_manager
from .root import (
    get_root_config_path,
    list_existing_root_config_files,
    read_root_config,
    read_root_section,
    write_root_config,
    write_root_section,
)

__all__ = [
    "ConfigManager",
    "get_default_config_manager",
    "get_root_config_path",
    "list_existing_root_config_files",
    "read_root_config",
    "read_root_section",
    "write_root_config",
    "write_root_section",
]
